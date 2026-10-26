import sys
import unittest
from unittest.mock import MagicMock, patch

from peach.mdns import (
    DnsSdPublisher, MdnsPublisher, answers_on_network, create_mdns_publisher, lan_ipv4,
)


class MdnsPublisherTests(unittest.TestCase):
    def test_registers_and_unregisters_peach_local_on_all_interfaces(self):
        with patch("peach.mdns.Zeroconf") as zeroconf_type, \
                patch("peach.mdns.answers_on_network", return_value=True):
            publisher = MdnsPublisher(
                "Peach.local", 80, address_resolver=lambda: "192.0.2.10"
            )
            publisher.start()
            info = zeroconf_type.return_value.register_service.call_args.args[0]
            self.assertEqual(info.server, "peach.local.")
            self.assertEqual(info.port, 80)
            self.assertEqual(publisher.status, "peach.local")
            self.assertEqual(publisher.backend, "zeroconf-all-interfaces")
            publisher.stop()
        zeroconf_type.assert_called_once_with()
        zeroconf_type.return_value.register_service.assert_called_once_with(
            info, allow_name_change=True,
        )
        zeroconf_type.return_value.unregister_service.assert_called_once_with(info)
        zeroconf_type.return_value.close.assert_called_once()

    def test_address_change_republishes_so_dhcp_moves_are_followed(self):
        """换 Wi-Fi、DHCP 换地址都只改地址、不通知进程。

        不复查的话 `peach.local` 会一直指向旧地址；zeroconf 的套接字也绑在旧接口上，
        所以是整体重建而不是只换地址字段。
        """
        addresses = iter(["192.0.2.10", "192.0.2.10", "198.51.100.7"])
        current = ["192.0.2.10"]

        def resolver():
            current[0] = next(addresses, current[0])
            return current[0]

        with patch("peach.mdns.Zeroconf") as zeroconf_type, \
                patch("peach.mdns.answers_on_network", return_value=True):
            publisher = MdnsPublisher(
                "peach", 80, address_resolver=resolver, refresh_seconds=0,
            )
            publisher.start()
            self.assertEqual(publisher.address, "192.0.2.10")
            self.assertEqual(publisher.refresh(), "unchanged")
            self.assertEqual(publisher.refresh(), "republished")
            self.assertEqual(publisher.address, "198.51.100.7")
            publisher.stop()
        self.assertEqual(zeroconf_type.call_count, 2)

    def test_losing_the_network_withdraws_the_record_instead_of_keeping_a_stale_one(self):
        """留着一条指向旧地址的记录比没有记录更糟：客户端会一直连一个不存在的地方。"""
        states = iter(["192.0.2.10"])

        def resolver():
            try:
                return next(states)
            except StopIteration:
                raise RuntimeError("no publishable LAN IPv4 address") from None

        with patch("peach.mdns.Zeroconf"), \
                patch("peach.mdns.answers_on_network", return_value=True):
            publisher = MdnsPublisher(
                "peach", 80, address_resolver=resolver, refresh_seconds=0,
            )
            publisher.start()
            self.assertEqual(publisher.status, "peach.local")
            self.assertEqual(publisher.refresh(), "unavailable")
            self.assertEqual(publisher.status, "unavailable")
            self.assertIsNone(publisher.address)

    def test_a_pinned_address_never_republishes(self):
        """生产上显式钉住地址时复查必须是空转，不能自己换成别的网卡。"""
        with patch("peach.mdns.Zeroconf") as zeroconf_type, \
                patch("peach.mdns.answers_on_network", return_value=True):
            publisher = MdnsPublisher(
                "peach", 80, address_resolver=lambda: "192.0.2.10", refresh_seconds=0,
            )
            publisher.start()
            self.assertEqual(publisher.refresh(), "unchanged")
            self.assertEqual(publisher.refresh(), "unchanged")
            publisher.stop()
        self.assertEqual(zeroconf_type.call_count, 1)

    def test_secure_publication_uses_https_service(self):
        with patch("peach.mdns.Zeroconf") as zeroconf_type, \
                patch("peach.mdns.answers_on_network", return_value=True):
            publisher = MdnsPublisher(
                "peach", 443, secure=True, address_resolver=lambda: "192.0.2.10"
            )
            publisher.start()
            info = zeroconf_type.return_value.register_service.call_args.args[0]
            self.assertEqual(info.type, "_https._tcp.local.")
            self.assertEqual(info.port, 443)
            publisher.stop()

    def test_rejects_invalid_hostname(self):
        with self.assertRaises(ValueError):
            MdnsPublisher("bad name", 80)

    def test_factory_picks_the_backend_that_works_on_this_platform(self):
        """macOS 走系统 mDNSResponder：自带 zeroconf 在 launchd 起的进程里会被
        「本地网络」隐私门静默拒绝，注册看着成功、一个多播包都发不出去。"""
        publisher = create_mdns_publisher("peach", 80)
        expected = DnsSdPublisher if sys.platform == "darwin" else MdnsPublisher
        self.assertIsInstance(publisher, expected)

    def test_explicit_address_avoids_tunnel_route(self):
        with patch("peach.mdns.Zeroconf") as zeroconf_type, \
                patch("peach.mdns.answers_on_network", return_value=True):
            publisher = MdnsPublisher(
                "peach", 80, address_resolver=lambda: "198.51.100.162", refresh_seconds=0,
            )
            publisher.start()
            self.assertEqual(publisher.address, "198.51.100.162")
            publisher.stop()
        zeroconf_type.assert_called_once_with()

    def test_tunnel_route_falls_back_to_real_host_interface(self):
        fake_socket = MagicMock()
        fake_socket.getsockname.return_value = ("198.18.0.1", 50000)
        with patch("peach.mdns.socket.socket", return_value=fake_socket), patch(
            "peach.mdns.socket.gethostname", return_value="host",
        ), patch(
            # 前两个是虚拟网卡那种网关端点（末段 1 或 2），`lan_ipv4` 按端点评分把它们
            # 排在真实接口后面，所以第三个胜出。
            "peach.mdns.socket.gethostbyname_ex",
            return_value=("host", [], ["192.0.2.2", "203.0.113.1", "198.51.100.162"]),
        ):
            self.assertEqual(lan_ipv4(), "198.51.100.162")


class ReachabilityTests(unittest.TestCase):
    """可达性不能由服务进程自证。

    macOS 的「本地网络」隐私门按进程判：launchd 起的作业自己发的多播会被静默丢弃，
    所以它去探自己必然收不到回应，会把好的记录判成不可达。运行时因此只报「注册还在
    生效」，真正的可达性验证交给 `scripts/check_mdns.py`——那个从终端跑，有权限。
    """

    def test_runtime_status_does_not_depend_on_a_self_probe(self):
        with patch("peach.mdns.Zeroconf"), \
                patch("peach.mdns.answers_on_network", return_value=False) as probe:
            publisher = MdnsPublisher(
                "peach", 80, address_resolver=lambda: "192.0.2.10", refresh_seconds=0)
            publisher.start()
            self.assertEqual(publisher.status, "peach.local")
            probe.assert_not_called()
            publisher.stop()

    def test_probe_never_raises_on_a_dead_socket(self):
        with patch("peach.mdns.socket.socket", side_effect=OSError("no multicast")):
            self.assertFalse(answers_on_network("peach.local", timeout=0.1))


if __name__ == "__main__":
    unittest.main()
