# Tabs

Display tab content.

---

## Default

```tsx
import { Tabs } from '@vercel/geistcn/components';
import { useState, type JSX } from 'react';

export function Component(): JSX.Element {
  const [selected, setSelected] = useState('apple');

  return (
    <Tabs
      selected={selected}
      setSelected={(t) => setSelected(t)}
      tabs={[
        { title: 'Apple', value: 'apple' },
        { title: 'Orange', value: 'orange' },
        { title: 'Mango', value: 'mango' },
      ]}
    />
  );
}
```

## Disabled

```tsx
import { Tabs } from '@vercel/geistcn/components';
import { useState, type JSX } from 'react';

export function Component(): JSX.Element {
  const [selected, setSelected] = useState('apple');

  return (
    <Tabs
      disabled
      selected={selected}
      setSelected={(t) => setSelected(t)}
      tabs={[
        { title: 'Apple', value: 'apple' },
        { title: 'Orange', value: 'orange' },
        { title: 'Mango', value: 'mango' },
      ]}
    />
  );
}
```

## Disable specific tabs

```tsx
import { Tabs } from '@vercel/geistcn/components';
import { useState, type JSX } from 'react';

export function Component(): JSX.Element {
  const [selected, setSelected] = useState('apple');

  return (
    <Tabs
      selected={selected}
      setSelected={(t) => setSelected(t)}
      tabs={[
        { title: 'Apple', value: 'apple' },
        { title: 'Orange', value: 'orange' },
        {
          title: 'Mango',
          value: 'mango',
          disabled: true,
          tooltip: 'Mangos are not allowed',
        },
      ]}
    />
  );
}
```

## With icons

```tsx
import { Tabs } from '@vercel/geistcn/components';
import {
  LogoIconBitbucket,
  LogoIconGithubSvg,
  LogoIconGitlabSvg,
} from '@vercel/geistcn-assets/logos';
import { useState, type JSX } from 'react';

export function Component(): JSX.Element {
  const [git, setGit] = useState('github');

  return (
    <Tabs
      selected={git}
      setSelected={(t) => setGit(t)}
      tabs={[
        { title: 'GitHub', value: 'github', icon: <LogoIconGithubSvg /> },
        { title: 'GitLab', value: 'gitlab', icon: <LogoIconGitlabSvg /> },
        {
          title: 'Bitbucket',
          value: 'bitbucket',
          icon: <LogoIconBitbucket colored />,
        },
      ]}
    />
  );
}
```

## Secondary

```tsx
import { Tabs } from '@vercel/geistcn/components';
import { useState, type JSX } from 'react';

export function Component(): JSX.Element {
  const [git, setGit] = useState('github');

  return (
    <Tabs
      selected={git}
      setSelected={(t) => setGit(t)}
      tabs={[
        { title: 'GitHub', value: 'github' },
        { title: 'GitLab', value: 'gitlab' },
        { title: 'Bitbucket', value: 'bitbucket', disabled: true },
      ]}
      variant="secondary"
    />
  );
}
```

## Best Practices

### When to use

* Use Tabs to switch between sibling views inside a single page (`Overview`, `Logs`, `Settings`).
* For navigation between unrelated pages, use a sub-menu, not Tabs. Tabs imply the views share scope, URL parent, and data model.
* Cap a Tabs row at 5–7 entries on desktop and 3–4 on mobile. Past that, consolidate or move secondary views behind a `Menu`.

### Behavior

* Selecting a tab is instant; don’t trigger network confirmation or toast on tab change.
* Reflect the active tab in the URL (query param or path) so deep-links and refresh restore state.
* Disable individual tabs only for permission or empty-state reasons; pair the disabled tab with a `tooltip` that names the constraint.

### Content

* `tabs[].title` is Title Case, 1–2 words, and names the destination noun (`Overview`, `Logs`, `Settings`). Verbs belong on buttons; `View Logs` is wrong on a tab.
* `tabs[].tooltip` is sentence case and explains the constraint (`Only visible to project owners.`), not the tab’s purpose.
* Don’t append a count to the title (`Logs (12)`); use a badge slot instead and drop the badge at zero.

### Accessibility

* Keep Left/Right arrows moving focus across tabs and Enter/Space activating; don’t hijack with global shortcuts.
* Label the tablist with `aria-label` when no visible heading sits above it (`aria-label="Sections"`).
* Maintain a visible focus ring on the active tab; don’t suppress focus styles for visual polish.
