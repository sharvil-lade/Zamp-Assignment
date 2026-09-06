# 16 · Frontend

React 18 + Vite, plain JSX, `react-router-dom`. No TypeScript, no Tailwind, no
state-management library. Ten screens, one API client, two hooks.

It talks to the REST API in `backend/routes/` and to nothing else. It is the only
UI: the server-rendered Jinja layer that preceded it was removed once React
covered every route, and the backend now serves this bundle at the root. Why it
went rather than staying as a fallback is in `10-assumptions-and-scope.md`.

## Structure

```
frontend/
├── index.html            the single page; loads src/main.jsx
├── vite.config.js        base: "/" — the backend serves the bundle at the
│                         root; also holds the vitest config
├── package.json          dev · build · preview · test · test:watch
├── .env.example          VITE_API_BASE
└── src/
    ├── main.jsx          mounts <App /> into #root
    ├── App.jsx           the route table — the security split lives here
    ├── api.js            the single API client
    ├── styles.css        one hand-written stylesheet
    ├── hooks/
    │   ├── useSession.jsx    who is signed in; asked once on boot
    │   └── useResource.js    loading · error · data · reload, per page
    ├── components/
    │   ├── Layout.jsx        the employee shell; gates on a session
    │   ├── SchemaForm.jsx    renders any form schema from data alone
    │   └── ui.jsx            Badge · Loading · ErrorBox · Empty · KeyValues
    ├── pages/
    │   ├── Login.jsx             credentials; there is no sign-up
    │   ├── Dashboard.jsx         stats, cases, runs, status filter
    │   ├── NewOnboarding.jsx     create a case, choose a form, copy link
    │   ├── OnboardingDetail.jsx  one case; regenerate its link
    │   ├── FormTemplates.jsx     every form a vendor can be sent
    │   ├── FormBuilder.jsx       edit one form, in place
    │   ├── DirectSubmit.jsx      the internal demo submission path
    │   ├── RunDetail.jsx         one run, end to end; polls while it runs
    │   ├── VendorForm.jsx        the public vendor portal
    │   └── NotFound.jsx
    └── __tests__/            vitest + @testing-library/react
```

| Directory | Holds | Does not hold |
|---|---|---|
| `hooks/` | The two pieces of state more than one page needs | Anything page-specific |
| `components/` | Presentation reused across pages | Any `fetch`, any business rule |
| `pages/` | One screen each, mapped one-to-one onto a route | A URL string, or a rule |
| `__tests__/` | Behaviour that must not regress | Snapshot tests |

## The API client is the only point of contact

`src/api.js` exports one object whose methods are the only URLs in the codebase.
No page calls `fetch`. Every request funnels through one `request()` function, so
four things are decided once instead of ten times:

- **Shared state.** `src/store.js` is a small zustand store holding the session
  flag and a cache of what the server last said, keyed per page (`dashboard:all`,
  `run:VS-0001`, `forms:list`). `useResource` reads it first, so returning to a
  page renders immediately and revalidates behind you instead of blanking out and
  redrawing the same table. It is deliberately **not** persisted — it holds bank
  details and decisions — and signing out empties it.
- **The credential.** `login` exchanges the shared password for a session token, `api.js` keeps it, and every later
  request carries `Authorization: Bearer <token>`. It is mirrored into
  `localStorage` so a reload does not sign the employee out, and it is dropped on
  any `401` — an expired or tampered token is worth nothing, so there is no point
  retrying with it. Signing out is `setToken(null)`: a stateless token cannot be
  revoked server-side, so forgetting it is the whole operation. The trade-off — a
  script on this origin could read it — is recorded in
  `10-assumptions-and-scope.md`.
- **The base path.** `${VITE_API_BASE}/api${path}`. A production bundle leaves
  `VITE_API_BASE` blank and calls its own origin; a dev bundle points at
  `http://localhost:8000`, which is the entire reason `CORS_ORIGINS` exists.
- **The error shape.** Any non-2xx becomes one `ApiError` carrying `status` and
  `detail`. A FastAPI `{"detail": "..."}` passes straight through; a 422
  validation list is flattened into one readable sentence; an unreachable backend
  becomes a status-0 `ApiError` rather than a raw fetch rejection. Because there
  is exactly one error type, `ui.jsx` can have exactly one `ErrorBox` and no page
  has to invent its own failure UI.
- **The body.** A JSON body gets serialised and typed; a `FormData` body is passed
  through untouched, so the browser sets the multipart boundary itself.

The practical payoff is that the things which must never regress — a missing
cookie, a wrong prefix, a case id leaking into a vendor URL — are tested once, in
`api.test.js`, rather than in every page.

## Session gating

Two pieces, and the boundary between them is the whole design.

**`SessionProvider`** (`hooks/useSession.jsx`) wraps the router. On mount it calls
`GET /api/session` exactly once and records the answer: an employee, or `null`.
That single call is also how a stored token is checked for expiry — only the
server can say whether one that survived a closed tab is still valid, and
`authenticated: false` is the answer for no token and a dead one alike. It
exposes `ready` separately from `employee`, because "we have not asked yet" and
"nobody is signed in" are different facts — conflating them flashes the login page
at a signed-in user on every reload. `signIn` and `signOut` call the API and update
the same state, so the header and the route guard can never disagree.

**`Layout`** (`components/Layout.jsx`) is the employee shell — header, navigation,
who is signed in, sign out — and it is also the guard. It renders a loading state
while `!ready`, redirects to `/login` when there is no employee (remembering where
the visitor was headed, so login can send them back), and otherwise renders its
`<Outlet />`.

`App.jsx` then makes the guarantee structural rather than a rule someone has to
remember:

```jsx
<Route path="/login" element={<Login />} />
<Route path="/vendor/onboard/:token" element={<VendorForm />} />

<Route element={<Layout />}>
  <Route path="/dashboard" element={<Dashboard />} />
  …every other employee page…
</Route>
```

Everything that needs a session is *nested inside* `Layout`. Adding a page inside
that block is authenticated by construction; there is no per-page check to forget.

### The vendor portal is routed outside the shell on purpose

`/vendor/onboard/:token` is a sibling of the guarded block, not a child of it.
That is a security boundary, not a layout preference:

- A vendor is never one render away from employee navigation. There is no header,
  no dashboard link, no reset button, and no code path that would add one.
- The token in the URL is the only authorisation the page carries. It never sends
  a case id, and the API would not accept one if it did — `store.get_case_by_token`
  looks a case up by the hash of the token and by nothing else.
- The page shows a vendor exactly two things: the form they were invited to fill
  in, and confirmation that it arrived. Never a decision, a finding, a risk level,
  an AI note, an audit event, a run or case id, or an employee's name.

`vendor.test.jsx` and `routing.test.jsx` assert all of that against the rendered
output — internal vocabulary, ids, links back into the employee app, and what is
sent on submit. The isolation lives in the route table, which is why there is no
per-page rule anyone has to remember.

## SchemaForm renders any form from data alone

`components/SchemaForm.jsx` takes a schema and renders it. That is the whole
component. It knows the shape `backend/forms.py` validates:

```jsonc
{ "sections": [ { "title": "…", "description": "…",
                  "fields": [ { "id", "label", "type", "required",
                                "options", "help", "canonical" } ] } ] }
```

and maps `type` onto a control: `document` to a file input, `textarea` to a
textarea, `select` to a select built from `options`, `boolean` to a checkbox, and
everything else to an `<input>` whose HTML type comes from a lookup table
(`email`, `tel`, `number`, `date`, `text`). `required` becomes the native
attribute and a starred label; `help` becomes a hint line under the control.

**`required` is a schema fact, not a component decision**, which is what lets the
standard form be honest about its conditional fields. `forms.standard_schema()`
sets it from `rules.ALWAYS_REQUIRED` and `rules.required_documents({})`, so
`gstin`, `pan`, `ifsc`, the PAN card and the GST certificate arrive with
`required: false` and render unstarred. A US vendor on an EIN can therefore
submit; R01 and R02 then judge those five conditionally from the answers given.
Had the star been hard-coded in React, the browser would block a submission the
engine would have accepted, with nothing on screen to explain why.

Two consequences worth stating:

- **Adding a field to a template needs no React change at all.** The builder
  writes it into the schema, the API returns the schema, `SchemaForm` renders it.
- **The same component renders the vendor's real form and the builder's preview.**
  `FormBuilder` mounts `SchemaForm` under a "Vendor's view" heading, so an author
  sees exactly what the vendor will see, because it is literally the same code.

Values are uncontrolled on purpose. The forms carry file uploads, so they submit
as `FormData` either way; mirroring every keystroke into React state would be work
in service of nothing. `prefill` seeds defaults, looked up by canonical name first
and field id second, so a case's known contact details are filled in without the
schema having to know where they came from.

## The form lifecycle, from an author's point of view

1. **See what exists.** `/forms` lists every form. *Standard Vendor Onboarding*
   carries a **Default** badge; it is seeded on first start-up and is what a new
   case uses when no other form is chosen. It cannot be deleted.
2. **Start one.** *New form* drops the author straight into the builder.
   *Duplicate* copies an existing form, which is the supported way to get a
   permanently different questionnaire — including a variant of the standard one.
3. **Edit it in place.** `/forms/:templateId` has two tabs, because an author only
   ever has two questions: what am I asking, and what will they see? Sections and
   fields are added, reordered and removed; a field can be marked `required`, given
   options and given help text; the **Vendor's view** tab is the same
   `SchemaForm` the vendor gets. **Save** writes the form
   (`PUT /api/forms/templates/{id}`), and the next case created uses it as it
   stands.
4. **Map a field to a canonical, or do not.** This is the choice that decides
   whether a deterministic rule ever runs. Picking a canonical makes the field
   *be* that PS-2 field — it flows into `rules.py` unchanged. Leaving it blank
   means completeness, type and options are checked and nothing else. The builder
   says so in as many words; the server rejects a canonical it does not recognise.
5. **Send it.** `/onboardings/new` lists the forms and defaults to the standard
   one. The case records the form id *and* a snapshot of its schema, so an edit
   made afterwards cannot shift the questions under a vendor who is part-way
   through, and the submission stays readable against what was actually asked.

**There are no versions, and that is the simplification worth naming.** An
immutable published version existed to guarantee that what a vendor was asked
never changes after the fact. The per-case snapshot gives exactly that guarantee,
so the version list, the draft/published states and the publish step were all
removed — along with the failure mode where an author edits a form, forgets to
publish, and wonders why the vendor got the old one.

**The standard form is the one exception to "edited in place is final."**
`store.seed_standard_template()` runs on every start-up and rebuilds it from
`forms.standard_schema()` if it either stops validating or falls behind the
engine — `forms.missing_engine_fields()` returning anything means the form no
longer asks for something the rules read. Reordering it, rewording a label or
adding a custom question survives; letting it drop a canonical field does not. An
author who wants those changes to be permanent duplicates it first.

The client mirrors `forms.validate_schema` — duplicate field ids, a select with no
options, a malformed id — purely for immediate feedback. **The server validates
again on every save, and its message is what the author is shown.**
The mirror is a convenience; it is not the check.

## Tests

`npm run test` from `frontend/`. Vitest in jsdom, `@testing-library/react`, no
network and no running backend — `src/api.js` is mocked with `vi.mock`, so what is
under test is what React does with a payload, never the payload itself.
`__tests__/setup.js` handles cleanup and the two APIs jsdom does not implement
(`window.confirm` and `navigator.clipboard`).

One file per concern, and each file exists to protect a specific claim:

| File | Protects |
|---|---|
| `api.test.js` | The bearer header on every request, that a 401 discards the token, the `/api` prefix, JSON vs multipart bodies, one error shape, and that a case id never appears in a vendor URL |
| `auth.test.jsx` | The login page and the session gate: an anonymous visitor is redirected, a failed login shows the server's message and never reveals whether the address exists, signing out clears the stored token and the session, a failed session check reads as signed out rather than crashing |
| `routing.test.jsx` | The route table through the real `<App />` — employee pages inside the shell, the vendor portal and login outside it, the root redirect, and a not-found page instead of a blank screen |
| `dashboard.test.jsx` | The metrics, the columns and what each number counts. It exists to catch a redesign that quietly drops a column |
| `run.test.jsx` | That polling stops on `finished` and not on the status, that the AI is presented as advisory and never as the decider, and that the send gate needs a human |
| `forms.test.jsx` | The builder: what an author can change, what the client validates before the server sees it, and that a save edits the form rather than forking it |
| `vendor.test.jsx` | The vendor boundary: every schema field renders, nothing internal leaks, a replayed link reads as already received, and the token is the only thing sent |

The backend suite (`python -m pytest tests -q` from the repository root) covers
the other side of the same boundary: the deterministic rules, the pipeline,
access tokens and what they unlock, form schemas and the per-case snapshot, and
the REST layer itself —
including that no SQL and no database driver appears in `backend/routes/`, and that
everything under `/api` answers in JSON while every other path serves the bundle.
