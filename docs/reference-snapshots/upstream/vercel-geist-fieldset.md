# Fieldset

Groups related form controls inside a bordered card with optional footer actions.

---

## Default

```tsx
import {
  Fieldset,
  FieldsetContent,
  FieldsetFooter,
  FieldsetFooterActions,
  FieldsetFooterAction,
  FieldsetFooterStatus,
  FieldsetSubtitle,
  FieldsetTitle,
  Link,
} from '@vercel/geistcn/components';
import type { JSX } from 'react';

export function Component(): JSX.Element {
  return (
    <Fieldset>
      <FieldsetContent>
        <FieldsetTitle>Account Settings</FieldsetTitle>
        <FieldsetSubtitle>
          Manage your account preferences and settings
        </FieldsetSubtitle>
      </FieldsetContent>
      <FieldsetFooter>
        <FieldsetFooterStatus>
          <span>
            Need help?{' '}
            <Link href="#" variant="highlight">
              View documentation
            </Link>
          </span>
        </FieldsetFooterStatus>
        <FieldsetFooterActions>
          <FieldsetFooterAction>Save Changes</FieldsetFooterAction>
        </FieldsetFooterActions>
      </FieldsetFooter>
    </Fieldset>
  );
}
```

## Disabled

```tsx
import {
  Fieldset,
  FieldsetContent,
  FieldsetFooter,
  FieldsetSubtitle,
  FieldsetTitle,
} from '@vercel/geistcn/components';
import type { JSX } from 'react';

export function Component(): JSX.Element {
  return (
    <Fieldset>
      <FieldsetContent disabled>
        <FieldsetTitle>Transfer Project</FieldsetTitle>
        <FieldsetSubtitle>
          Move this project to another team or account
        </FieldsetSubtitle>
      </FieldsetContent>
      <FieldsetFooter highlight>
        <span className="text-copy-14 text-gray-900">
          You need additional permissions to transfer projects.
        </span>
      </FieldsetFooter>
    </Fieldset>
  );
}
```

## With Long Content

```tsx
import {
  Fieldset,
  FieldsetContent,
  FieldsetFooter,
  FieldsetFooterActions,
  FieldsetFooterAction,
  FieldsetFooterStatus,
  FieldsetSubtitle,
  FieldsetTitle,
} from '@vercel/geistcn/components';
import type { JSX } from 'react';

export function Component(): JSX.Element {
  return (
    <Fieldset>
      <FieldsetContent>
        <FieldsetTitle>Privacy Policy</FieldsetTitle>
        <FieldsetSubtitle>
          Lorem ipsum dolor sit amet, consectetur adipiscing elit. Nullam
          volutpat, nunc vel ultrices sollicitudin, dolor eros volutpat ex, et
          sagittis sem enim in eros. Curabitur eu consequat neque, non finibus
          odio. Donec vitae tellus eu mauris feugiat efficitur. Maecenas blandit
          sit amet tellus vel blandit. Phasellus ultrices vulputate arcu, vel
          dictum erat facilisis et.
        </FieldsetSubtitle>
      </FieldsetContent>
      <FieldsetFooter>
        <FieldsetFooterStatus>
          <span>Last updated: March 10, 2025</span>
        </FieldsetFooterStatus>
        <FieldsetFooterActions>
          <FieldsetFooterAction variant="secondary">
            Decline
          </FieldsetFooterAction>
          <FieldsetFooterAction>Accept</FieldsetFooterAction>
        </FieldsetFooterActions>
      </FieldsetFooter>
    </Fieldset>
  );
}
```

## Multiple Fieldsets

```tsx
import {
  Fieldset,
  FieldsetContent,
  FieldsetFooter,
  FieldsetFooterActions,
  FieldsetFooterAction,
  FieldsetFooterStatus,
  FieldsetSubtitle,
  FieldsetTitle,
} from '@vercel/geistcn/components';
import type { JSX } from 'react';

export function Component(): JSX.Element {
  return (
    <div className="flex flex-col gap-6">
      <Fieldset>
        <FieldsetContent>
          <FieldsetTitle>Personal Information</FieldsetTitle>
          <FieldsetSubtitle>
            Update your name and contact details
          </FieldsetSubtitle>
        </FieldsetContent>
        <FieldsetFooter>
          <FieldsetFooterStatus>
            <span>This information will be publicly visible</span>
          </FieldsetFooterStatus>
          <FieldsetFooterActions>
            <FieldsetFooterAction>Update</FieldsetFooterAction>
          </FieldsetFooterActions>
        </FieldsetFooter>
      </Fieldset>

      <Fieldset>
        <FieldsetContent>
          <FieldsetTitle>Security</FieldsetTitle>
          <FieldsetSubtitle>
            Manage your password and authentication methods
          </FieldsetSubtitle>
        </FieldsetContent>
        <FieldsetFooter>
          <FieldsetFooterStatus>
            <span>Last password change: 30 days ago</span>
          </FieldsetFooterStatus>
          <FieldsetFooterActions>
            <FieldsetFooterAction>Change Password</FieldsetFooterAction>
          </FieldsetFooterActions>
        </FieldsetFooter>
      </Fieldset>

      <Fieldset>
        <FieldsetContent disabled>
          <FieldsetTitle>API Access</FieldsetTitle>
          <FieldsetSubtitle>
            Generate and manage API tokens for programmatic access
          </FieldsetSubtitle>
        </FieldsetContent>
        <FieldsetFooter highlight>
          <span className="text-copy-14">
            You need a Pro account to access API features.
          </span>
        </FieldsetFooter>
      </Fieldset>
    </div>
  );
}
```

## Without Footer

```tsx
import {
  Fieldset,
  FieldsetContent,
  FieldsetSubtitle,
  FieldsetTitle,
} from '@vercel/geistcn/components';
import type { JSX } from 'react';

export function Component(): JSX.Element {
  return (
    <Fieldset>
      <FieldsetContent>
        <FieldsetTitle>Account Information</FieldsetTitle>
        <FieldsetSubtitle>
          Lorem ipsum dolor sit amet, consectetur adipiscing elit. Nullam
          euismod, nisl eget aliquam ultricies, nisl nisl aliquam nisl.
        </FieldsetSubtitle>
      </FieldsetContent>
    </Fieldset>
  );
}
```

## Without Title

```tsx
import {
  Fieldset,
  FieldsetContent,
  FieldsetFooter,
  FieldsetFooterStatus,
  FieldsetSubtitle,
} from '@vercel/geistcn/components';
import type { JSX } from 'react';

export function Component(): JSX.Element {
  return (
    <Fieldset>
      <FieldsetContent>
        <FieldsetSubtitle>
          This fieldset contains only a subtitle with no title. It can be used
          for informational sections or supplementary content.
        </FieldsetSubtitle>
      </FieldsetContent>
      <FieldsetFooter>
        <FieldsetFooterStatus>
          <span>Information only</span>
        </FieldsetFooterStatus>
      </FieldsetFooter>
    </Fieldset>
  );
}
```

## With Error Text

```tsx
import {
  ErrorText,
  Fieldset,
  FieldsetContent,
  FieldsetFooter,
  FieldsetFooterActions,
  FieldsetFooterAction,
  FieldsetFooterStatus,
  FieldsetSubtitle,
  FieldsetTitle,
} from '@vercel/geistcn/components';
import type { JSX } from 'react';

export function Component(): JSX.Element {
  return (
    <Fieldset>
      <FieldsetContent>
        <FieldsetTitle>API Configuration</FieldsetTitle>
        <FieldsetSubtitle>
          Configure your API endpoint and authentication
        </FieldsetSubtitle>
        <div className="mt-4">
          <ErrorText>
            API key validation failed. Please check your credentials and try
            again.
          </ErrorText>
        </div>
      </FieldsetContent>
      <FieldsetFooter>
        <FieldsetFooterStatus>
          <span>Last checked: 5 minutes ago</span>
        </FieldsetFooterStatus>
        <FieldsetFooterActions>
          <FieldsetFooterAction>Verify API Connection</FieldsetFooterAction>
        </FieldsetFooterActions>
      </FieldsetFooter>
    </Fieldset>
  );
}
```

## With Warning Text

```tsx
import {
  Fieldset,
  FieldsetContent,
  FieldsetFooter,
  FieldsetFooterActions,
  FieldsetFooterAction,
  FieldsetFooterStatus,
  FieldsetSubtitle,
  FieldsetTitle,
  WarningText,
} from '@vercel/geistcn/components';
import type { JSX } from 'react';

export function Component(): JSX.Element {
  return (
    <Fieldset>
      <FieldsetContent>
        <FieldsetTitle>Database Settings</FieldsetTitle>
        <FieldsetSubtitle>
          Configure your database connection parameters
        </FieldsetSubtitle>
        <div className="mt-4">
          <WarningText>
            Changing these settings will require a restart of your application.
            Make sure to save any pending work.
          </WarningText>
        </div>
      </FieldsetContent>
      <FieldsetFooter>
        <FieldsetFooterStatus>
          <span>Current status: Connected</span>
        </FieldsetFooterStatus>
        <FieldsetFooterActions>
          <FieldsetFooterAction variant="secondary">
            Cancel
          </FieldsetFooterAction>
          <FieldsetFooterAction>Apply Changes</FieldsetFooterAction>
        </FieldsetFooterActions>
      </FieldsetFooter>
    </Fieldset>
  );
}
```

## With Disabled Wall

```tsx
import {
  DisabledWall,
  Fieldset,
  FieldsetContent,
  FieldsetFooter,
  FieldsetSubtitle,
  FieldsetTitle,
} from '@vercel/geistcn/components';
import type { JSX } from 'react';

export function Component(): JSX.Element {
  return (
    <Fieldset>
      <FieldsetContent disabled>
        <FieldsetTitle>Advanced Features</FieldsetTitle>
        <FieldsetSubtitle>
          Access premium capabilities and tools
        </FieldsetSubtitle>
        <div className="relative mt-2 p-3 border rounded">
          <p className="text-copy-14">
            This content is behind a disabled wall and not accessible to free
            users.
          </p>
          <p className="mt-2 text-copy-14">
            It contains advanced configuration options and premium features.
          </p>
          <DisabledWall />
        </div>
      </FieldsetContent>
      <FieldsetFooter highlight>
        <span className="text-copy-14">
          Upgrade to a Pro plan to access these features.
        </span>
      </FieldsetFooter>
    </Fieldset>
  );
}
```

## Error Type

```tsx
import {
  Fieldset,
  FieldsetContent,
  FieldsetFooter,
  FieldsetFooterActions,
  FieldsetFooterAction,
  FieldsetFooterStatus,
  FieldsetSubtitle,
  FieldsetTitle,
} from '@vercel/geistcn/components';
import type { JSX } from 'react';

export function Component(): JSX.Element {
  return (
    <Fieldset type="error">
      <FieldsetContent>
        <FieldsetTitle>Payment Failed</FieldsetTitle>
        <FieldsetSubtitle>
          Your payment method was declined. Please update your billing
          information to continue using the service.
        </FieldsetSubtitle>
      </FieldsetContent>
      <FieldsetFooter>
        <FieldsetFooterStatus>
          <span>Payment failed on February 10, 2026</span>
        </FieldsetFooterStatus>
        <FieldsetFooterActions>
          <FieldsetFooterAction variant="secondary">
            Contact Support
          </FieldsetFooterAction>
          <FieldsetFooterAction>Update Payment Method</FieldsetFooterAction>
        </FieldsetFooterActions>
      </FieldsetFooter>
    </Fieldset>
  );
}
```

## Warning Type

```tsx
import {
  Fieldset,
  FieldsetContent,
  FieldsetFooter,
  FieldsetFooterActions,
  FieldsetFooterAction,
  FieldsetFooterStatus,
  FieldsetSubtitle,
  FieldsetTitle,
} from '@vercel/geistcn/components';
import type { JSX } from 'react';

export function Component(): JSX.Element {
  return (
    <Fieldset type="warning">
      <FieldsetContent>
        <FieldsetTitle>Trial Ending Soon</FieldsetTitle>
        <FieldsetSubtitle>
          Your trial period will end in 3 days. Add a payment method to continue
          accessing premium features without interruption.
        </FieldsetSubtitle>
      </FieldsetContent>
      <FieldsetFooter>
        <FieldsetFooterStatus>
          <span>Trial expires: February 13, 2026</span>
        </FieldsetFooterStatus>
        <FieldsetFooterActions>
          <FieldsetFooterAction variant="secondary">
            Remind Me Later
          </FieldsetFooterAction>
          <FieldsetFooterAction>Add Payment Method</FieldsetFooterAction>
        </FieldsetFooterActions>
      </FieldsetFooter>
    </Fieldset>
  );
}
```
