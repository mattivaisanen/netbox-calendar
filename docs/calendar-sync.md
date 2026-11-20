# Calendar sync

Calendar sync puts NetBox events straight into each user's own calendar:
**Outlook** (Microsoft 365 or Outlook.com), **Google Calendar** (Google
Workspace or a personal account), or any **CalDAV** server (Nextcloud,
Fastmail, iCloud, SOGo, Zimbra, Radicale, Baïkal, mailbox.org, ...). It is
the option to use when a calendar can't subscribe to the iCal feed, which
happens when NetBox is only reachable inside your network.

- Each user connects their own calendar on **Calendar → Calendar Feed**.
  For Outlook and Google they sign in with their own account and NetBox
  gets *delegated* access to that one account. For CalDAV they give a
  server address and an app password.
- NetBox creates a calendar of its own in the account (named **NetBox**
  by default) and keeps it up to date. Users can colour it, hide it or
  delete it without touching their own events.
- Sync is one way, from NetBox to the calendar. Edits made there are not
  sent back, and the next change in NetBox overwrites them.
- Only NetBox's server talks to the calendar service, over outbound HTTPS.
  The sign-in redirect runs in the user's browser, so NetBox needs no
  address that can be reached from the internet. A CalDAV server inside
  your network needs no internet at all.

Each provider is off until you turn it on, and you can turn on any mix
of them.

## Outlook setup

You do this once for your organization. It needs someone who can create
app registrations in Microsoft Entra ID and, in most tenants, grant admin
consent.

### 1. Find NetBox's redirect URI

Set **NetBox address** (`netbox_url`) on **Calendar → Settings** to the
address users open NetBox at. Then open **Calendar → Calendar Sync** and
copy the **Redirect URI**. It looks like this:

```text
https://netbox.corp.internal/plugins/calendar/sync/outlook/callback/
```

The address has to match exactly, including `https` and the trailing
slash. An internal host name works, because only the browser opens it.

### 2. Register the app in Entra

In the [Microsoft Entra admin center](https://entra.microsoft.com):

1. Go to **Identity → Applications → App registrations → New
   registration**.
2. **Name:** for example `NetBox Calendar`. Users see this name when they
   consent.
3. **Supported account types:**
   - *Accounts in this organizational directory only* (single tenant),
     for users of your organization only. This is the usual choice.
   - *Accounts in any organizational directory* (multitenant), when users
     from several tenants use the same NetBox.
   - *Any organizational directory and personal Microsoft accounts*, when
     users also sign in with Outlook.com accounts. Set `outlook_tenant` to
     `'common'`.
4. **Redirect URI:** platform **Web**, and the address from step 1.
   Entra accepts plain `http://` only for `localhost`, for trying it out
   on a local NetBox.
5. Press **Register**. On the app's **Overview** page, copy the
   **Application (client) ID** and the **Directory (tenant) ID**.

### 3. Add a client secret

**Certificates & secrets → Client secrets → New client secret.** Pick an
expiry your policy allows and copy the secret's **Value** (not its ID).
Microsoft shows it only once.

Write down the expiry date. When the secret expires, every connection
shows an error (*AADSTS7000222*) until you add a new secret and update
`configuration.py`. Users don't need to connect again after that.

### 4. Allow the calendar permission

**API permissions → Add a permission → Microsoft Graph → Delegated
permissions.** Add these permissions:

| Permission | Why |
|---|---|
| `Calendars.ReadWrite` | Create the NetBox calendar and its events in the user's own mailbox. |
| `offline_access` | Keep syncing when the user isn't signed in to NetBox. |
| `openid`, `profile`, `email` | Show which account is connected. |

Don't add application permissions. NetBox never needs them.

Then press **Grant admin consent for <your organization>**, so users don't
each have to ask for approval. If your tenant lets users consent to apps
themselves, this step is optional: users see a consent prompt the first
time they connect.

### 5. Configure NetBox

Put the secret in `configuration.py`. It can't be set in NetBox, so it
never appears on a page:

```python
PLUGINS_CONFIG = {
    'netbox_calendar': {
        'netbox_url': 'https://netbox.corp.internal',
        'outlook_sync': True,
        'outlook_client_id': '00000000-0000-0000-0000-000000000000',
        'outlook_client_secret': os.environ.get('NETBOX_CALENDAR_OUTLOOK_SECRET', ''),
        # Your Directory (tenant) ID, or your domain such as 'corp.example'.
        'outlook_tenant': '11111111-1111-1111-1111-111111111111',
    },
}
```

Restart NetBox **and its worker** (`rqworker`), which does the pushing.
You can also turn on **Outlook sync** and set the client ID and tenant on
**Calendar → Settings**. **Calendar → Calendar Sync** lists the settings
that are still missing.

If NetBox reaches the internet through a proxy, set NetBox's own
`HTTP_PROXIES` setting. The sync uses it.

## Google Calendar setup

You do this once. It needs a Google Cloud project; for Google Workspace,
an admin of the Workspace.

1. In the [Google Cloud console](https://console.cloud.google.com), pick
   or create a project and enable the **Google Calendar API** (*APIs &
   Services → Library*).
2. **APIs & Services → OAuth consent screen**: user type **Internal**
   for a Workspace organization (only its users can connect, and no
   review by Google is needed), or **External** for personal accounts.
   Add the scope `.../auth/calendar.app.created`.
3. **APIs & Services → Credentials → Create credentials → OAuth client
   ID**, type **Web application**. Under **Authorized redirect URIs** add
   the Google redirect URI from **Calendar → Calendar Sync**, such as
   `https://netbox.corp.internal/plugins/calendar/sync/google/callback/`.
   Google accepts `http://` only for `localhost`.
4. Copy the client ID and client secret into `configuration.py`:

```python
PLUGINS_CONFIG = {
    'netbox_calendar': {
        'google_sync': True,
        'google_client_id': '1234567890-abc.apps.googleusercontent.com',
        'google_client_secret': os.environ.get('NETBOX_CALENDAR_GOOGLE_SECRET', ''),
    },
}
```

5. Restart NetBox and its worker.

NetBox asks only for `calendar.app.created`: it can create calendars and
manage the ones it made, and can't read or change anything else in the
account. An *External* app in testing mode works only for the test users
you list on the consent screen, and its sign-ins expire after 7 days;
publish the app to avoid that.

NetBox's server reaches `oauth2.googleapis.com` and `www.googleapis.com`.

## CalDAV setup

Turn on **CalDAV sync** on **Calendar → Settings** (`caldav_sync`). There
is nothing to register.

- **CalDAV server** (`caldav_server`) empty: each user types their
  server's address. NetBox then connects to whatever address a user gives,
  including hosts inside your network, so leave it empty only when you
  trust your NetBox users with that.
- **CalDAV server** set, such as your company's Nextcloud
  `https://cloud.corp.internal/remote.php/dav`: users only give their user
  name and app password.

NetBox finds the user's calendar home on the server and creates a
**NetBox** calendar in it. An address that points at one calendar is used
as it is, and disconnecting then removes only NetBox's events from it.

Server addresses that work:

| Server | Address |
|---|---|
| Nextcloud | `https://<host>/remote.php/dav` |
| Fastmail | `https://caldav.fastmail.com/dav/` |
| iCloud | `https://caldav.icloud.com/` |
| mailbox.org | `https://dav.mailbox.org/` |
| SOGo | `https://<host>/SOGo/dav/` |
| Radicale, Baïkal and others | the server's address, or the address of one calendar |

Users should make an **app password** (Nextcloud: *Settings → Security*;
Fastmail: *Settings → Privacy & Security → App passwords*; iCloud:
*appleid.apple.com → App-Specific Passwords*). NetBox stores it encrypted.
A server with an internal CA certificate needs that CA trusted by NetBox's
Python (`REQUESTS_CA_BUNDLE`).

## Connecting a calendar (users)

1. Open **Calendar → Calendar Feed**. Each calendar your admin turned on
   has a card.
2. **Outlook** or **Google Calendar**: press **Connect**, sign in with your
   account and accept the permissions. **CalDAV**: give the server address
   (unless your admin set it), your user name and an app password, and
   press **Connect**.
3. Back in NetBox, the card shows *Active*. Your events appear in a
   calendar called **NetBox** within a minute or two.

Under **Events**, choose what goes in:

- **Events I own or am on** (the default): events you own, and events
  where you, or a group you belong to, have a role.
- **All events I can see**: every event you may view in NetBox.

**Sync Now** pushes everything again. **Disconnect** stops the sync and,
unless you untick the box, deletes the NetBox calendar. You can connect
several services at once.

## What gets synced

Outlook:

| In NetBox | In Outlook |
|---|---|
| Single event | One event |
| Repeating event | A series with the same rule |
| Skipped occurrence | Deleted from the series |
| Moved occurrence | Moved within the series |
| Rule Outlook can't express (hourly, or "1st Monday and 3rd Friday") | Single events from 30 days ago to a year ahead, extended every hour |
| Cancelled event | Removed |
| Event that ended more than 30 days ago | Removed |
| All-day event | All-day event |
| Impact *None* | Shown as free |
| Status *Planned* | Shown as tentative |
| Other status | Shown as busy |
| Reminders | One Outlook reminder, at the earliest reminder time |
| Event type | Category |
| Linked objects, owner, impact, link | Location and body |

Google Calendar gets the iCal rule as it is: a series with the same
`RRULE`, skipped occurrences as `EXDATE`s, and moved occurrences changed in
place. Up to five reminders. Impact *None* is shown as free, status
*Planned* as tentative.

CalDAV gets each event as the same `.ics` the feed has, so every rule,
skipped and moved occurrence arrives exactly. No attendees are included,
so the server sends nobody an invitation.

On all of them, cancelled events and events that ended more than 30 days
ago are removed. Times are sent in NetBox's `TIME_ZONE`, so a repeating event keeps its
time when daylight saving time starts or ends.

## When it runs

- After every save of an event, its objects, people or occurrences, a
  background job pushes that event to every connected calendar.
- Every hour, the **Calendar sync** system job checks every event against
  what was last sent, and sends only what changed. It also catches changes
  the per-save job missed, such as the automatic status changes.
- Changing an event's time or rule replaces it in Outlook. Changing its
  text updates it in place.

## Admin page

**Calendar → Calendar Sync** (superusers) shows:

- whether the sync is turned on, which settings are missing, and the
  redirect URI to register;
- every connected user, with their account, status, last sync, the number
  of events synced, the last problem, and **Re-sync** and **Disconnect**
  buttons.

## Problems

Outlook:

| Shown | Meaning and fix |
|---|---|
| *Sign-in expired* | The refresh token stopped working. Causes: a password reset, a revoked session, a Conditional Access change, more than 90 days without a sync, or a changed NetBox `SECRET_KEY`. The user gets a bell notification and presses **Connect Again**. |
| *AADSTS50011* during sign-in | The redirect URI doesn't match the app registration. Copy it again from **Calendar → Calendar Sync**. |
| *AADSTS65001* or *Need admin approval* | Users may not consent to apps in your tenant. Grant admin consent (step 4). |
| *MailboxNotEnabledForRESTAPI* | The account has no Exchange Online mailbox (no licence). Use an account with a mailbox. |
| *AADSTS50020* or *AADSTS9002331* | The account type doesn't match: a personal account needs the app to allow personal accounts and `outlook_tenant` set to `'common'`. |
| *AADSTS700016* | Wrong client ID, or the app is registered in another tenant than `outlook_tenant`. |
| *AADSTS7000222* or *AADSTS7000215* | The client secret expired or is wrong. Add a new one (step 3) and restart NetBox and its worker. |
| *ErrorAccessDenied* | The `Calendars.ReadWrite` permission is missing. |
| An event shows an error | That event failed. The others went through, and it is tried again on the next sync. |

Google and CalDAV:

| Shown | Meaning and fix |
|---|---|
| *redirect_uri_mismatch* | Add the Google redirect URI from **Calendar → Calendar Sync** to the OAuth client. |
| *access_denied* or *org_internal* | The account is outside the Workspace of an *Internal* app, or not a test user of an app in testing. |
| *Sign-in expired* (Google) | The user removed NetBox under their Google account's third-party access, or an app in testing mode passed 7 days. Connect again. |
| *The server refused the user name or password* | Wrong user name or app password, or an expired app password. Connect again with a new one. |
| *No calendars found* | The address isn't the server's CalDAV address. Give the address of one calendar instead. |
| *The server would not create a calendar* | Make a calendar yourself on the server and connect with its address. |

## Security

- Tokens are stored encrypted with a key derived from NetBox's
  `SECRET_KEY`. Changing the key makes them unreadable, and users connect
  again.
- Client secrets are read from `configuration.py` only. CalDAV app
  passwords are stored encrypted like tokens.
- OAuth sign-in uses the authorization code flow with PKCE and a
  one-time state, so a sign-in can't be completed from another browser.
- Users can revoke NetBox's access themselves on
  [myaccount.microsoft.com](https://myaccount.microsoft.com) under *Apps
  and services*, and admins can revoke it under **Enterprise applications**.

## Outlook in national clouds

For Microsoft's government or China clouds, set the sign-in and Graph
addresses in `configuration.py`. For example, for US Government L4:

```python
'outlook_login_url': 'https://login.microsoftonline.us',
'outlook_graph_url': 'https://graph.microsoft.us/v1.0',
```

## Other ways into a calendar

- **iCal feed** (Calendar → Calendar Feed): works with any calendar app
  that can reach NetBox. See [usage.md](usage.md#in-outlook-and-other-calendars).
- **E-mail invites**: with direct e-mail on, notices arrive as calendar
  invites. Any mail client can add them to any calendar, with no app
  registration. See [notifications.md](notifications.md#direct-e-mail).
- **Teams or Slack channels**: an Event Rule on the calendar's notices
  with a webhook posts to a channel. See
  [notifications.md](notifications.md#event-rules-and-webhooks).
- **Classic Outlook for Windows and Apple Calendar** download the feed
  from the user's own computer, so an internal feed address works there.
