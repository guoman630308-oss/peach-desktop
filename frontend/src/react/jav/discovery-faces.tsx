import { avatarInner } from '../../card-art/markup';
import { entityPath } from '@peach/legacy/core';
import { SourceAvatar } from '../components/source-avatar';
import type { Discovery } from './jav';

/** 发现人物使用馆藏的共演头像几何与悬停动作。 */
export function DiscoveryFaces({ item }: { item: Discovery }) {
  const people: NonNullable<Discovery['performer_refs']> = item.performer_refs?.length ? item.performer_refs
    : [...new Set(item.performers.split(/[,，、;/]/).filter(Boolean))].map(name => ({ name }));
  const faces = people.slice(0, 5).map(person => {
    const face = person.source_avatar && person.source_actor_id && !person.has_image
      ? { children: <SourceAvatar code={item.code} person={person} /> }
      : { dangerouslySetInnerHTML: { __html: avatarInner(person.name, person, null) } };
    return person.id
      ? <a key={person.name} data-media-avatar="" href={entityPath('performer', person.name, person.id)} aria-label={`打开女优页：${person.name}`} {...face} />
      : <span key={person.name} data-media-avatar="" title={person.name} {...face} />;
  });
  return people.length > 1 ? <div data-media-avatars="">{faces}</div> : faces[0] || null;
}
