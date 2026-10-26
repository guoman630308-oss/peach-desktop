import { useQuery } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { firstGrapheme } from '@peach/legacy/core';
import { apiGet } from '../../api';

/** 来源头像与详情共用查询缓存；取图失败时保留首字。 */
export function SourceAvatar({ code, person, detail = false }: {
  code: string; person: { name: string; source_actor_id?: string }; detail?: boolean;
}) {
  const node = useRef<HTMLSpanElement>(null);
  const [visible, setVisible] = useState(detail || typeof IntersectionObserver === 'undefined');
  useEffect(() => {
    if (!node.current || visible) return;
    const observer = new IntersectionObserver(entries => {
      if (entries.some(entry => entry.isIntersecting)) { setVisible(true); observer.disconnect() }
    }, { rootMargin: '100px' });
    observer.observe(node.current);
    return () => observer.disconnect();
  }, [visible]);
  const result = useQuery({ queryKey: ['jav-actor-image', code, person.source_actor_id],
    enabled: visible,
    queryFn: ({ signal }) => apiGet<{ ok: boolean; image: string }>(
      `/api/jav/image?code=${encodeURIComponent(code)}&image=${encodeURIComponent('actor:' + person.source_actor_id)}`, signal),
    staleTime: 6 * 3600_000 });
  const face = <><span ref={node} data-source-initial="">{firstGrapheme(person.name)}</span>{result.data?.image
    ? <img src={result.data.image} alt="" data-drop="self" /> : null}</>;
  return detail ? <span data-id-face="">{face}</span> : face;
}
