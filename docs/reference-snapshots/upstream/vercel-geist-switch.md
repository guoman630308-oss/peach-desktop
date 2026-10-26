# Switch

Choose between a set of options.

---

## Default

Ensure the width of each item is wide enough to prevent jumping when active.

```tsx
import { Switch, SwitchControl } from '@vercel/geistcn/components';
import type { JSX } from 'react';

export function Component(): JSX.Element {
  return (
    <div className="flex relative min-w-px max-w-full flex-col items-start flex-1">
      <Switch name="default">
        <SwitchControl defaultChecked label="Source" value="source" />
        <SwitchControl label="Output" value="output" />
      </Switch>
    </div>
  );
}
```

## Disabled

```tsx
import { Switch, SwitchControl } from '@vercel/geistcn/components';
import type { JSX } from 'react';

export function Component(): JSX.Element {
  return (
    <div className="flex relative min-w-px max-w-full flex-col items-start flex-1">
      <Switch name="view-mode">
        <SwitchControl defaultChecked disabled label="Source" value="source" />
        <SwitchControl disabled label="Output" value="output" />
      </Switch>
    </div>
  );
}
```

## Sizes

```tsx
import { Switch, SwitchControl } from '@vercel/geistcn/components';
import type { JSX } from 'react';

export function Component(): JSX.Element {
  return (
    <div className="flex relative min-w-px max-w-full flex-col lg:flex-row lg:flex-wrap flex-1">
      <div className="flex relative min-w-px max-w-full flex-col items-start flex-1">
        <Switch name="sizes-small" size="small">
          <SwitchControl defaultChecked label="Source" value="source" />
          <SwitchControl label="Output" value="output" />
        </Switch>
      </div>

      <div className="flex relative min-w-px max-w-full flex-col items-start flex-1">
        <Switch name="sizes-default">
          <SwitchControl defaultChecked label="Source" value="source" />
          <SwitchControl label="Output" value="output" />
        </Switch>
      </div>

      <div className="flex relative min-w-px max-w-full flex-col items-start flex-1">
        <Switch name="sizes-large" size="large">
          <SwitchControl defaultChecked label="Source" value="source" />
          <SwitchControl label="Output" value="output" />
        </Switch>
      </div>
    </div>
  );
}
```

## Full width

```tsx
import { Switch, SwitchControl } from '@vercel/geistcn/components';
import type { JSX } from 'react';

export function Component(): JSX.Element {
  return (
    <Switch name="full-width" style={{ width: '100%' }}>
      <SwitchControl
        defaultChecked
        label="Source"
        size="large"
        value="source"
      />
      <SwitchControl label="Output" size="large" value="output" />
    </Switch>
  );
}
```

## Tooltip

```tsx
import { Switch, Tooltip, SwitchControl } from '@vercel/geistcn/components';
import type { JSX } from 'react';

export function Component(): JSX.Element {
  return (
    <div className="flex relative min-w-px max-w-full flex-col items-start flex-1">
      <Switch name="view-mode">
        <Tooltip desktopOnly text="View Source">
          <SwitchControl
            defaultChecked
            label="Source"
            name="tooltip"
            size="large"
            value="source"
          />
        </Tooltip>
        <Tooltip desktopOnly text="View Output">
          <SwitchControl
            label="Output"
            name="tooltip"
            size="large"
            value="output"
          />
        </Tooltip>
      </Switch>
    </div>
  );
}
```

## Icon

```tsx
import { Switch, SwitchControl } from '@vercel/geistcn/components';
import type { JSX } from 'react';
import {
  IconGridSquare,
  IconListUnordered,
} from '@vercel/geistcn-assets/icons';

export function Component(): JSX.Element {
  return (
    <div className="flex relative min-w-px max-w-full flex-col lg:flex-row lg:flex-wrap flex-1">
      <div className="flex relative min-w-px max-w-full flex-col items-start flex-1">
        <Switch name="icons-small" size="small">
          <SwitchControl
            defaultChecked
            icon={<IconGridSquare />}
            value="source"
          />
          <SwitchControl icon={<IconListUnordered />} value="output" />
        </Switch>
      </div>

      <div className="flex relative min-w-px max-w-full flex-col items-start flex-1">
        <Switch name="icons-default">
          <SwitchControl
            defaultChecked
            icon={<IconGridSquare />}
            value="source"
          />
          <SwitchControl icon={<IconListUnordered />} value="output" />
        </Switch>
      </div>

      <div className="flex relative min-w-px max-w-full flex-col items-start flex-1">
        <Switch name="icons-large" size="large">
          <SwitchControl
            defaultChecked
            icon={<IconGridSquare />}
            value="source"
          />
          <SwitchControl icon={<IconListUnordered />} value="output" />
        </Switch>
      </div>
    </div>
  );
}
```

## Best Practices

* Use Switch as a segmented selector for 2–3 mutually exclusive options that show different views of the same surface, like `Source` vs `Output`.
* For a boolean on/off setting, use `Toggle`. Switch implements radio semantics, so two options stay mutually exclusive rather than reading as a checkbox.
* Past 3 options or when labels grow beyond a couple of words, switch to `Tabs` or a `Select`.
* Pass a `name` so the underlying radios are grouped; without it, more than one option can appear selected at once.
* Set `defaultChecked` (or controlled `checked`) on exactly one `Switch.Control` so the group has a defined initial state.
* Pad each `Switch.Control` so the widest label fits without the active pill resizing on selection. Test with the longest copy in the set.
* Title Case each `Switch.Control` label. Keep labels to one or two words and parallel: `Source` / `Output`, not `Source` / `Show output`.
* Always provide a `label` on every `Switch.Control`, even when an `icon` carries the meaning, so screen readers announce the option. The component renders it as `geist-sr-only` for icon-only controls.
* Pair an icon-only Switch with a `Tooltip` on each control so sighted users get the same label assistive tech receives.
