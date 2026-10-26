import { createEntityController, type EntityControllerPorts } from './entity-controller';
import { createEntityLoading, type EntityLoadingPorts } from './entity-loading';
import type { EntityPageActions, EntityPageHelpers } from './entity-page';
import { entityPortraitImg } from './entity-portrait';
import { createEntityEntryState } from './entity-route';
import { createPhotoSurfaceController, type PhotoSurfacePorts } from './photo-surface';

export interface EntityApplicationPorts {
  controller: EntityControllerPorts;
  loading: EntityLoadingPorts;
  photos: PhotoSurfacePorts;
  helpers: Omit<EntityPageHelpers, 'portraitImg'>;
  actions: Pick<EntityPageActions, 'toggleTag' | 'openFollowAuthor' | 'javContext'>;
  sortPreference(): { sort: string; dir: string };
}

/** 资料页地址、动作、等待与照片墙共享一个生命周期；打开意图只消费一次。 */
export function createEntityApplication(ports: EntityApplicationPorts) {
  const entry = createEntityEntryState();
  const helpers: EntityPageHelpers = { ...ports.helpers, portraitImg: entityPortraitImg };
  const controller = createEntityController(ports.controller, helpers, ports.actions);
  return {
    ...controller,
    helpers,
    fresh: entry.fresh,
    addressProps(kind: string, name: string, search: string) {
      const { filters, media } = entry.read(kind, name, search, ports.sortPreference());
      return controller.props(kind, name, filters, media);
    },
    loading: createEntityLoading(ports.loading),
    photos: createPhotoSurfaceController(ports.photos),
  };
}
