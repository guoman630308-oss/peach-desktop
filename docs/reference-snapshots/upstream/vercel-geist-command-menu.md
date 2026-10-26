# Command Menu

Launch a set of actions as a full-screen overlay.

---

## Default

```tsx
import {
  CommandMenu,
  Button,
  CommandMenuInput,
  CommandMenuList,
  CommandMenuGroup,
  CommandMenuItem,
} from '@vercel/geistcn/components';
import { useState, type JSX } from 'react';

export function Component(): JSX.Element {
  const [open, setOpen] = useState(false);

  function callback(): void {
    // no op
  }

  return (
    <>
      <Button onClick={() => setOpen(true)}>Open Command Menu</Button>
      <CommandMenu open={open} setOpen={setOpen}>
        <CommandMenuInput placeholder="What do you need?" />
        <CommandMenuList>
          <CommandMenuGroup heading="Suggestions">
            <CommandMenuItem callback={callback}>Figma Import</CommandMenuItem>
          </CommandMenuGroup>
          <CommandMenuGroup heading="Commands">
            <CommandMenuItem callback={callback}>
              Import Extension
            </CommandMenuItem>
            <CommandMenuItem callback={callback}>
              Manage Extensions
            </CommandMenuItem>
          </CommandMenuGroup>
          <CommandMenuGroup heading="Collaboration">
            <CommandMenuItem callback={callback}>
              Flags Explorer
            </CommandMenuItem>
          </CommandMenuGroup>
        </CommandMenuList>
      </CommandMenu>
    </>
  );
}
```

## With divider

```tsx
import {
  Button,
  CommandMenu,
  CommandMenuDivider,
  CommandMenuGroup,
  CommandMenuInput,
  CommandMenuItem,
  CommandMenuList,
} from '@vercel/geistcn/components';
import { useState, type JSX } from 'react';

export function Component(): JSX.Element {
  const [open, setOpen] = useState(false);

  function callback(): void {
    // no op
  }

  return (
    <>
      <Button onClick={() => setOpen(true)}>Open Command Menu</Button>
      <CommandMenu open={open} setOpen={setOpen}>
        <CommandMenuInput placeholder="What do you need?" />
        <CommandMenuList>
          <CommandMenuItem callback={callback}>Item 1</CommandMenuItem>
          <CommandMenuItem callback={callback}>Item 2</CommandMenuItem>
          <CommandMenuDivider />
          <CommandMenuItem callback={callback}>Item 3</CommandMenuItem>
          <CommandMenuGroup heading="Group 1">
            <CommandMenuItem callback={callback}>
              Grouped Item 1
            </CommandMenuItem>
            <CommandMenuItem callback={callback}>
              Grouped Item 2
            </CommandMenuItem>
          </CommandMenuGroup>
        </CommandMenuList>
      </CommandMenu>
    </>
  );
}
```

## With suffix

```tsx
import {
  Button,
  CommandMenu,
  CommandMenuGroup,
  CommandMenuInput,
  CommandMenuItem,
  CommandMenuList,
} from '@vercel/geistcn/components';
import { IconCheckCircle } from '@vercel/geistcn-assets/icons';
import { useState, type JSX } from 'react';

export function Component(): JSX.Element {
  const [open, setOpen] = useState(false);

  function callback(): void {
    // no op
  }

  return (
    <>
      <Button onClick={() => setOpen(true)}>Open Command Menu</Button>
      <CommandMenu open={open} setOpen={setOpen}>
        <CommandMenuInput placeholder="What do you need?" />
        <CommandMenuList>
          <CommandMenuGroup heading="Group 1">
            <CommandMenuItem
              callback={callback}
              suffix={<p className="text-copy-14 text-gray-700">USA</p>}
            >
              United States of America
            </CommandMenuItem>
            <CommandMenuItem
              callback={callback}
              suffix={<p className="text-copy-14 text-gray-700">ESP</p>}
            >
              Spain
            </CommandMenuItem>
            <CommandMenuItem
              callback={callback}
              suffix={<p className="text-copy-14 text-gray-700">FRA</p>}
            >
              France
            </CommandMenuItem>
          </CommandMenuGroup>
          <CommandMenuGroup heading="Group 2">
            <CommandMenuItem
              callback={callback}
              suffix={<p className="text-copy-14 text-gray-700">AUT</p>}
            >
              Austria
            </CommandMenuItem>
            <CommandMenuItem
              callback={callback}
              suffix={<IconCheckCircle color="gray-700" />}
            >
              Switzerland
            </CommandMenuItem>
            <CommandMenuItem
              callback={callback}
              suffix={<p className="text-copy-14 text-gray-700">GER</p>}
            >
              Germany
            </CommandMenuItem>
          </CommandMenuGroup>
        </CommandMenuList>
      </CommandMenu>
    </>
  );
}
```

## Best Practices

### When to use

* Use CommandMenu for a global, keyboard-first command palette that finds resources and runs actions across the app.
* For a menu opened from a visible trigger on a single resource, use `Menu`; for right-click on a row, use `ContextMenu`.
* Group items into pages (`Projects`, `Team Settings`) when a single flat list would exceed roughly 30 items or span multiple resource types.

### Behavior

* Bind `⌘K` on macOS and `Ctrl+K` elsewhere. Don’t reuse the binding for any in-page filter input; it’s a global shortcut.
* Open into the root page; preserve the search query when navigating back from a sub-page so the user’s typing isn’t lost.
* Trap focus inside the overlay while it’s open and return focus to the previously active element on close.
* Show a recent or default item set when the input is empty so the menu is useful before typing.

### Content

* `<CommandMenu.Item>` children are Title Case verb phrases (`Deploy Project`, `Invite Team Member`). Avoid navigation phrasing like `Go to project page`; CommandMenu commands act, not browse.
* `CommandMenuPage.label` is Title Case and names the scope (`Projects`, `Team Settings`).
* `CommandMenuPage.placeholder` is sentence case, action-oriented, and ends with `…` (`Search projects…`, `Type a command or search…`). Bare `Search…` is wrong because it doesn’t name the scope.
* `<CommandMenu.Group heading>` is Title Case, 1–2 words (`Actions`, `Recent`).

### Accessibility

* Use `aria-live="polite"` on the result count so screen readers hear how the list narrows as the user types.
* Up/Down arrows move highlight, Enter activates, Escape closes. Backspace at an empty input pops the page stack.
* Display each item’s `keybind` as a `Kbd` slot so the shortcut is discoverable to sighted users and announced as a label to screen readers.
