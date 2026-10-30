# Introduction

We are creating a new SaaS FastAPI application to create conference and fair presentation posters and allow their creators to print them and share them with the public.
The application is configured as a Docker container and runs under Docker on the local machine and in Google Cloud Run.
We are going to build it in stages, and by feature, section, and page.

# Data Storage

The database is PostgreSQL equipped with PGVector. 
On-going chats are stored in Postgres to allow review of and continuation between sessions.
Sessions are managed with Redis (locally installed).

# Session Management

Sessions persist for {SESSION_MAX_AGE}.
Users are notified of incipient session expiration at {SESSION_EXPIRATION_NOTIFICATION=480}.
When a session is terminated, the session's associated chat websocket must also be closed with an appropriate message.

# Cookie Identity

A cookie is used to keep track of the last user and welcome them back by name when they return.

# Server Startup

Determine the installed themes in /static/l

# Payment Processing 

Uses Stripe in cloud and simulates purchases on the local machine.

# Poster Management

Posters are either unpurchased or purchased. 
Unpurchased posters cannot be downloaded.
Purchased posters can be downloaded as digitally native PDFs.
A user can have only one unpurchased poster at a time, but any number of purchased posters.

`poster.resumption` is nullable text storing a saved URL, including query parameters.
It follows `uid` in the schema definition and is appended when upgrading an existing
database. Routes can call `await common.resume(request, url)` to save it for the
signed-in user's session-selected poster. The helper verifies ownership, preserves
the URL verbatim (including `request.url` objects). Omitting `url` or passing `None`
saves the current request's full URL, including query parameters. It reports missing selection,
missing posters, and database failures explicitly. Pages do not save it automatically.
The `clear: bool = True` argument appends `xyzzy=arpa` to the saved URL's query
string (before any fragment). Pass `clear=False` to save the URL unchanged.
Call `await common.clear_resume(request)` to explicitly clear the selected,
owned poster's resumption URL to NULL, regardless of any query marker.

# Schema

A preliminary schema is in `schema/schema.sql`. It needs work.

In schema.sql, modify the source and section tables so that conferences support full-text search, along with embedding.
See search.md for an explanation of search goals and implementation guidelines.

# Routers and Endpoints

Place each router in its own file with any local-to-it functions

Place any router-specific javascript in /static/javascript/{router}/router.js
Place any page-specific javascript in /static/javascript/pages/{page}.js

Place the Wikkit router stylesheet in /static/css/router.css.
Place any page-specific CSS in /static/css/pages/{page}.css

# Router: Wikkit the Wizard

**`/wikkit`**
Redirects to `/wikkit/magic`, or to the same-origin URL supplied as a POST parameter.
Every page loads Wikkit's code and stylesheet through the shared page template.
Wikkit creates a draggable, resizable in-page element (#chatbot) with z-index 9999,
a scrollable chat log, and a fixed-height contentEditable input region.
There is no iframe.

User input messages are moved from the contentEditable region to the bottom of the scrollable region before being sent to the server over the websocket.
Each page opens its own websocket connection to the chatbot and announces its page name.
Navigation closes that connection; the destination page opens a new one.

Client-side code lives in `/static/javascript/wikkit.js`.
Whenever the chatbot's content changes, the client clones and serializes its content
into the tab's `sessionStorage` under `wikkit.chatbot`, including live form values.
Position and dimensions are saved initially and on moves, resizes, and page exit.
On each new page, Wikkit restores that content and geometry (or uses an empty log/input
and default geometry). Restored controls have their event handlers reattached.
Hidden-chat and closed-input state are restored too. Storage failures are logged.

When a page loads, it calls `connect(listen)` to initialize its local Wikkit instance.
The listen parameter is a callback that delivers chatbot messages to the client page.
`connect(listen)` returns a JavaScript object, `apiobj`, which should probably be a singleton, that exposes two methods to the client:

# Client-side functions

1 **`apiobj.say(html)`**
Pass a text or an HTML fragement to `say()` to have it wrapped in <div class="chat"></div> tags and added as a chatbot message to the end of the #chatbot div message stream (above the contentEditable region).

2 **`apiobj.command(html, echo=false)`**
Pass text directly to the server via the websocket.
If echo is true then also call `apiobj.say(html)`.

# Chatbot Server-client Interactions

User input sent to the server are processed there as intents using the logic in intents.py with results sent back.
Results sent back can contain a message to the user and/or a command to the client:

{
	"user" : string,
	"client" : {
		"action" : string,
		"payload" : (optional string)
	}
}

Currently the only valid command is 'redirect' where the payload is the URL for the chatbot to navigate to.
And see `Wikkit.md`

The current chatbot protocol and page-specific `.does` command registries are
specified in [Chatbot Details](./%23%20Chatbot%20Details.md). New replies use
`say` and `command: {does, payload}`; the client continues accepting the legacy
`user`/`client` format above.

# Web Page

Each web page can have its own CSS in `/static/css/pages/{page}.css` and JavaScript in `/static/javascript/pages/{page}.js`. The page name is a stable, lowercase slug supplied by the route. Shared head templates conditionally include each file when it exists, in addition to the CSS and JavaScript already loaded by the core template.

# Session, Poster, and User Memory Request Availability

During requests, `session`, `poster`, and `user` memories are available at request.state.session, request.state.poster, and request.state.user respectively.

# Basic Menu

Provide a simple navigation menu for the web page, allowing users to access the main sections such as Home, Pricing, Help, Account, and Sign-in or Sign-out as appropriate, depending on the user's authentication status.

The menu should be included in the shared head template and be consistent across all pages.

# Home Page

**`/`** 
Redirects to `/wikkit/magic`

# Wikkit Router

## Main Landing Page
**`/wikkit/magic`**

### Template Content
Displays a landing page.
Shows `/static/graphics/wikkit.svg` on the left with {APPLICATION_NAME} large Wikkit's font on the right.

### Internal Logic
If the user is returning (as indicated by the presence of the user cookie):
- redirect to `/account/signin`

Wikkit Says:f"""
<div>
Welcome to {APPLICATION_NAME}. I&rsquo;m Wikkit, {APPLICATION_NAME}&rsquo;s resident wizard.
</div>
<div>
With a few minutes and a little magic, we can quickly and easily prepare your conference or fair poster.
</div>
<p>
Can I have your name to start?
</p>
"""

tries = 3
while tries > 0:
- Wait for the user to respond.
- If the user provides a non-empty response:
	- Use common.clean_string to clean the user's response.
	- Store the cleaned response in request.state.user['user-name'] as `user-name`.
	- Wikkit Says:f"""
<p>
Nice to meet you, {request.state.user['user-name']}!
</p>
"""
	- break
- tries -= 1
- if tries == 0:
- wikkit_says(f"""
<p>
I&rsquo;m sorry, I didn't catch your name. 
You can always tell me later.
For now I&rsquo;ll call you <em>Friend</em>.
</p>
""")
- Set request.state.user['user-name'] to 'Friend'
- break

- Wikkit Says:f"""
	<p>
	I&rsquo;m sorry, I didn&rsquo;t catch your name. Could you please tell it to me?
	</p>
	"""

Store the user's name also in a cookie so that returning users can be recognized and greeted appropriately.

Redirect to `/account/register`

## Service Pricing Breakdown

**`/wikkit/pricing`**
### Template Content 
Placeholder content about posters being paid for only when ready for printing.

### Internal Logic

Wikkit Says:f"""
<p>
Here is our pricing information, {request.state.user['user-name']}. 
As you can see, it&rsquo;s really simple: you pay only when you&rsquo;re ready to download your print-ready poster.
</p>
"""

Wait for user input.
Use common.clean_response to clean the user's response.
If response indicates the user has further questions:
	Wikkit Says:f"""
<p>
Sure, what else would you like to know?
</p>
"""
	Wait for user input.
	Use common.clean_response to clean the user's response.


## Help Page 

**`/wikkit/help`**
### Template Content

Empathize with the user's situation and offer assistance.
What can I help you with?

- Link to account page.
- Link to password reset.
- Link to contact support.

### Internal Logic

Wikkit Says:f"""
<p>
What  can I help you with, {request.state.user['user-name']}?
</p>
"""

# Account Router

## Account Registration Page

**`/account/register`**
### Internal Logic
Allows a user to create an account.
Requires a unique email address and a password.
Use AJAX to try creating the account.

Show an informative message on the screen if the username or the email are not unique.

Sends an email with a time-limited confirmation link whose value is based on the recovery timestampz set in the user's database record, to the new account's address using local SMPT support or SMTP2Go when running in the cloud (*Need to register for SMTP2Go*)

### Template Content

Form with username, password, email.

### Wikkit says:

f"""
<p>Please fill out and submit the registration form so that we can get started on your poster.</p>
"""

## Account confirmation

**`/account/confirmation`**

### Template Content

Thank the user by name for completing the registration process.

### Wikkit Says:

f"""
<p>
Congratulations, {request.state.user['user-name']}. 
We&rsquo;re all set.
</p>
<p>
Let&rsquo;s get started on your poster!"
</p>

## Account Sign-in

**`/account/signin`**
### Template Content
Display a welcome back to {APPLICATION_NAME}, {cookie.username}.

"Please sign-in to continue working on your poster."

Includes /static/graphics/wikkit.svg on the left with {APPLICATION_NAME} in a large font on the right.

Form for the user to enter their email and password to sign in does an AJAX submission to /account/validate via POST.
- The form should include a submit button.
- There should also be a forgotten password button that redirects to `/account/recover` to allow the user to reset their password, passing their email address as a query parameter if they've entered it into the form.

### Internal Logic
Set session.username to cookie.username

Wikkit Says:f"""
<p>
Welcome back, {request.state.user['user-name']} to {APPLICATION_NAME}! 
I&rsquo;m happy to see you again.
</p>
<p>
Please sign in and we&rsquo;ll continue working on your poster.
</p>
"""

Uses `/account/validate` for AJAX submission of the sign-in form.
- After successful sign-in, as indicated from the response of `/account/validate`,
  redirect using `await account.resumption_page(request)`. It reads
  `poster.resumption` for the signed-in user's session-selected poster and clears
  that database field to NULL only if the saved URL has the query assignment
  `xyzzy=arpa`. Otherwise it leaves the field set. The returned URL retains the
  marker. If there is no selected, owned poster, redirect
  to `/conferences/find`; an existing poster without a valid local or same-origin
  resumption URL falls back to `/posters/list`. Query parameters are preserved.

**`/account/validate`**
### Internal Logic

Validate the user's email and password.
If valid:
- set request.state.user to the authenticated user
- Wikkit says:
f"""
<p>
Let&rsquo;s get back to work on your poster!
</p>
"""
- return a success response.
Elif cookie.username:
- Wikkit says:
f"""
<p>
I&rsquo;m sorry, {cookie.username}, I don&rsquo;t recognize your email or password. 
Please try again.
</p>
"""
Else:
- Wikkit says:
	f"""
	<p>
	I&rsquo;m sorry, I don&rsquo;t recognize that email or password. 
	Please try again.
	</p>
	"""
- return an error response.
"""

## Account Sign-out

**`/account/signout`**
## Template Content
Shows a page confirming that the user has been signed out of {APPLICATION_NAME}.Sign out of an active account.
Shows a page telling the user that they've been signed out of {APPLICATION_NAME}.
Provides a link back to `/home`.

## Internal Logic
Clear the user's session.
Wikkit says:
f"""
<p>
Okay, {request.state.user['user-name']}, you have been signed out of {APPLICATION_NAME}.
</p>
<p>
I hope to see you again soon.
</p>
"""

## Account Password Change

**`/account/password`**
Change user's password feature.

## Account Settings Update (placeholder)

**`/account/update`**
change password
delete acccount


## Password Recovery

**`/account/recover`**
### Internal Logic
Recover password using user&rsquo;s email address.

Wikkit says:
f"""
<p>
Please tell me your email address, {request.state.user['user-name']}, so I can send you a recovery link.
</p>
"""
Send a recovery link to the user's email address using MailJet API.
Mock up the email sending process for development and testing purposes.

### Template Content
Form for the user's email address

## Password Recovered

**`/account/recovered`**
### Internal Logic
Validate the user's password recovery link.
If the link is valid then redirect to `/account/password`.

### Template Content
Appropriate messaging.
Form for the user's email address with a submit button,,

## Account Deletion

**`/account/delete`**
### Internal Logic

### Template Content

### Wikkit Says

f"""
<p>
{request.state.user['user-name']}, please provide your email address to reset your password.
</p>

# Posters Router

`PosterStore.unpurchased()` is the common owner-scoped draft check.
`POST /posters/check` uses it before the New Poster button proceeds.
New posters receive an independently random theme from the class selectors in
`static/css/themes.css` and font set from `static/css/fonts.css`. Both are saved
atomically with the new poster, then applied in the layout editor and read-only
previews. Reusing an existing draft preserves its styling; no reassignment occurs.
- If it does: 
	- Have Wikkit say f"I&rsquo;m sorry, {request.state.user['user-name']}, you can work on only one poster at a time.
- Else:
	- return `/posters/create` as the AJAX redirect; the client posts to it to create a new unpurchased poster

# Posters Router

**`/posters/list`**
### Internal Logic
Shows a list of the posters the user has created.
The list is sortable by poster name, conference date and supports the semantic search logic described in search.md
The list can be displayed as a vertically scrolling list or an array of large thumbnails.
The current unpurchased poster, if any, is highlighted.
Clicking on an unpurchased poster redirects to `/layout/edit`.
Clicking on purchased posters displays them completely filling the viewport, scrollable and zoomable from full-size to both height and width being smaller than the viewport's dimensions. A close affordance allows the user to return to the posters list.
Posters can be optionally deleted with confirmation by clicking on a per-poster deletion affordance.
Has a button, "New Poster" which when clicked checks whether the user has an existing unpurchased poster.
- If it does: 
	- Wikkit says:
f"""
I&rsquo;m sorry, {request.state.user['user-name']}, you can work on only one poster at a time.
Would you like me to open it for you?
<button data-sends="yes">Yes</button>
&nbsp;
<button data-sends="no">Not now</button>

"""
	- wait for user input
	- clean the user response with common.clean_response()
	- if "Yes":
		- redirect to `/posters/open`
	- if "No":
		- do nothing
- Else:
	- Post to `/posters/create`, which atomically rechecks and creates the draft.
	- Follow its AJAX redirect to `/content/upload`.

========================================

### Implemented Posters Router Contract

- `GET /posters/list` provides owner-only list/grid views, name/conference-date
  sorting, and hybrid search as documented in [search.md](./search.md).
  The conference date is the earliest start date among conferences showing the poster.
- `POST /posters/check` checks for an existing unpurchased poster using
  `PosterStore.unpurchased()`. Existing drafts return HTTP 409 with Wikkit's
  one-poster-at-a-time message and Yes/Not now chat buttons without changing the
  selection. The list waits for a chat reply and posts it to `/posters/open`.
  Otherwise it posts to `/posters/create` and follows its AJAX redirect.
- `POST /posters/open` uses `common.clean_response()` on the optional `response`.
  Affirmative replies (or a direct request without a response) select the user's
  current draft, set `request.state.poster['pid']` and `poster_id`, and return
  Wikkit's "Here is your current unpurchased poster" message and the AJAX redirect
  `/layout/edit?pid={pid}`. Nonaffirmative replies leave the selection unchanged.
  `GET /posters/open` selects the same owner-scoped draft, sends an HTTP 303
  redirect, and queues the message for the next Wikkit-rendered page.
- `POST /posters/create` replaces `POST /posters/new` and the old
  `GET /posters/create` initialization page. It locks the owner's user row and
  reuses the draft check in that transaction so concurrent requests cannot
  create multiple drafts. An existing draft is selected, Wikkit says
  "I'm sorry, {name}! You already have an existing unpurchased poster. Here it is.",
  and the AJAX redirect is `/layout/edit?pid={pid}`.
  Otherwise it creates a basic poster with nullable content fields set to NULL
  (including dimensions, overriding the schema default), selects it in the
  server-side session and request state, and returns `/content/upload`.
  Search vectors remain trigger-maintained. Layout and theme are not initialized.
  Drafts without dimensions show an explicit preview-unavailable placeholder.
  Both creation and checking use same-origin JSON POST requests, not GET navigation.
  The content-upload destination is implemented below. **The layout editor
  remains deferred**.
- `GET /posters/{pid}/open` checks ownership, selects the draft in the server-side
  session, and redirects to `/layout/edit`. **The layout editor is deliberately
  deferred**.
- `GET /posters/{pid}/view` opens purchased posters in a full-viewport viewer
  outside the Wikkit shell. Fit and 100% zoom, intermediate zoom, scrolling, and
  Close/Escape are supported. The read-only HTML preview renders stored title,
  authors, and text sections; it is not a print/export renderer or layout editor.
  `GET /posters/{pid}/preview` also supplies owner-only draft thumbnails.
- `POST /posters/delete` accepts same-origin JSON with `pid` and returns Wikkit's
  confirmation question with Yes/No buttons without rendering a template. The
  list waits for a chat reply, then posts `pid` and `response` to the same
  endpoint. A server-side pending poster ID binds the reply to the prompt;
  `common.clean_response()` must recognize "yes" to delete. Other replies
  cancel. Both results return `deleted` and Wikkit's message; the list removes
  the card only on deletion. Deletion removes dependent rows and poster memory
  and clears the selected poster when appropriate. Source files are removed
  before database deletion; already-missing local files or GCS objects do not
  block deletion or retries. Other storage failures preserve the poster record
  and return an explicit error. The legacy
  `POST /posters/{pid}/delete` endpoint continues to require `confirmed: true`.
- Poster identity is revalidated before HTTP/websocket memory initialization.
  Authentication, owner filtering, and confirmation are enforced on the server.
- Apply `schema/schema.sql` before enabling poster search. Run the focused tests
  with `bin/python -m unittest tests.test_posters`. Set `POSTERS_TEST_DATABASE_URL` to
  enable PostgreSQL integration tests; those use a temporary isolated schema and
  never truncate the public schema.

**`/posters/open`**
### Internal Logic
Get the pid of the user's current unpurchased poster,
Set request.state.poster['pid'] to the retrieved poster ID
Wikkit says:
f"Here is your current unpurchased poster, {request.state.user['user-name']}."
Redirect to `/layout/edit` with the poster ID as a query parameter, e.g., `/layout/edit?pid={request.state.poster['pid']}`

**`POST /posters/create`**
### Internal Logic
If the user already has an unpurchased poster:
- Wikkit says f"I&rsquo;m sorry, {request.state.user['user-name']}! You already have an existing unpurchased poster. Here it is."
- Redirect to `/layout/edit` with the poster ID as a query parameter, e.g., `/layout/edit?pid={request.state.poster['pid']}`

Create a basic poster entry with null values for all nullable fields.
Set request.state.poster['pid'] to the newly created poster ID
Redirect to `/content/upload`

**`/posters/delete`**
### Template Content
(None)

### Internal Logic
Wikkit Says: 
f"""
Are you sure you want to delete "{request.state.poster['title']}?"
<p class="centered">
	<button class="action" data-sends="yes">Yes</button> &nbsp;
	<button class="action" data-sends="no">No</button>
</p>
"""

Wait for user input
Uses common.clean_response()
If the user confirms: 
- proceed with deletion.
- Wikkit says "Okay, I deleted your poster."
- Return confirmation
Else:
- Wikkit says "Okay, I won&rsquo;t delete your poster."
- Return cancellation

# Conferences Router

### Implemented Conferences Router Contract

- `conferences.get_conference_info(criteria)` runs a web-search structured
  OpenAI request and returns only conferences or fairs with a parseable start
  date on or after today (or `None`). A missing end date uses the start date.
  Poster requirements are unified into `dimensions`, `width`, `height` and
  `units` using the first specified value; results include `event_type`
  (`conference` or `fair`). No shared module state is mutated per request.
- `POST /conferences/find` accepts same-origin JSON `{"criteria": "..."}` (up to
  2000 characters) from a signed-in user and returns the matching conferences.
  Search failures return HTTP 503. `GET /conferences/find` (the post-sign-in
  destination when no poster is selected) redirects to `/conferences/query`.
- `GET /conferences/query` requires a signed-in user with a selected,
  owner-checked, unpurchased draft; otherwise it redirects to sign-in or
  `/posters/list`. Its `conferences-query` Wikkit workflow follows the logic
  below. Stored conferences are found with full-text search matching any of
  the user's terms. Web results are then added to the same enumerated table
  without duplicates. Rows can be clicked, or chosen by number or name.
  A failed database or web search is logged and reported in chat; the
  workflow then continues with any results it has.
- Selecting a conference stores a web result in `conference` unless a
  conference with the same normalized name and start date already exists.
  New rows use an empty administrator and password, `notify='never'`,
  `hideafter=0` and a NULL `verified`, and requirements go in `details`. The
  owner's unpurchased poster gets `poster.cid`, the conference is stored in
  poster memory as `conference`, and Wikkit redirects to `/content/upload`.
- `poster.cid` is a nullable integer foreign key to `conference.cid`. New
  posters leave it NULL until a conference is selected; selecting another
  conference replaces it. The schema also adds the column for older databases.
- Apply `schema/schema.sql` so conference search vectors are computed on
  insert. Run `bin/python -m unittest tests.test_conferences`; set
  `POSTERS_TEST_DATABASE_URL` for isolated PostgreSQL integration tests.

**`/conferences/find`**
### Template Content

### Internal Logic
Given a set of criteria describing a conference or fair, including its name, location, purpose, date or dates use conferences.get_conference_info() to find matching conferences

**`/conferences/query`**

### Internal Logic

Wikkit says: 
f"""
<div>Many conferences and fairs have specific requirements for poster designs.</div>
<div>If we can find your conference or fair, I can likely find any poster requirements for it.</div>
<div>Please provide any information you have about your conference or fair.</div>
<div>Besides its name, its location (some conferences and fairs occur in multiple cities), purpose, and dates are helpful.</div>
"""

Wait for user input.
Clean the input using common.clean_response()

If there is no user input or the user replied 'idontknow'  then:
- Wikkit says "That&rsquo;s okay, we can continue without conference information."
- redirect to `/content/upload`

While True:
- Check the database for any existing conferences or fairs that match the user input.
	Use full-text search or other relevant search methods to find matching conferences or fairs in the database.
- if any results were found, display them in a formatted, enumerated table:
	- Include columns for the conference or fair name, location, purpose, and dates.
	- Allow the user to click on a row to select a conference or fair.
	- If there is one result:
		- Use Levenshtein.ratio to determine indel similarity between the user input and the single result.
		- If the ratio is above 0.7, consider it a match
			- Wikkit say:
	f"""
	I found a conference or fair that I think is yours.
	Am I right? 
	<button sends="yes">Yes</button>
	&nbsp;
	<button sends="no">No</button>
	"""

			- Wait for user input.
			- if the user input is 'yes'
				- set request.state.poster.conference to the matched conference
				- redirect to `/content/upload`

	Use `/conferences/find` to find any matching conferences, passing the user input as the search criteria.
	- If no matching conferences or fairs are found
		- Wikkit says 
			f"""
I&rsquo;m sorry {request.state.user['user-name']} I couldn't find any matching conferences or fairs."
<div>
Can you add anything?
</div>
<button sends="yes">Yes</button>
&nbsp;
<button sends="no">No</button>
"""	
	- if user input is 'no':
		- Wikkit says "Okay, we will continue without conference information."
		- redirect to `/content/upload`

		- prompt the user to provide more information or continue without conference information.
	- Wait for user input.
	- Clean the user input using common.clean_response()

	- If the user provides additional information, repeat the search for matching conferences or fairs.
	prompt the user to provide more information or continue without conference information.


	Add the current conferences in the table with the newly found results.

When a conference is clicked;
- Add the conference to the conference table if it is unique.
- Redirect to `/content/upload`

```
If all found are conferences then scope = 'conference'
Elif all found are fairs then scope = 'fair'
Else scope = 'conference or fair'
```

### Wikkit Says:

f"""
<p>
{username}, please select the {scope} that best matches your poster requirements by clicking on it, entering its corresponding number, or telling me its name.
If there is no matching {scope}, just let me know and we&rsquo;ll move on.
</p>
<p>
If none of these are correct, just let me to continue without conference information.
</p>
Enter the number or the name of the {scope}
"""

# Note removed: The previous section about intents and intent mapping has been removed as it is now handled elsewhere.


# Content Router

Write a local function that takes a Python text or binary object and produces a hash from it.

### Implemented upload and processing contract

- `GET /content/upload` lists source files for the selected, owner-checked,
  unpurchased poster, with roots ordered chronologically and extracted children
  indented immediately below their parent. An empty list triggers the initial
  upload greeting; all visits show the supported-format and completion guidance.
- The page supports file selection and whole-page drag and drop. Each file is
  sent separately to `POST /content/upload` as `application/octet-stream`, with
  a percent-encoded basename in `X-Filename`. Transfers overlap, and users can
  select or drop more files while earlier transfers are still running.
  Each response confirms the original is saved; it does not wait for Docling
  extraction, summaries or image descriptions. Out-of-order responses are merged
  without losing uploaded rows. Failures are displayed per file without hiding
  other results. Finishing is blocked until all active transfers settle.
- Supported extensions: PDF, DOCX, PPTX, XLSX, XLSM, PNG, TIF/TIFF, JPG/JPEG,
  Pages, Numbers, Keynote (`.key`), and Markdown (`.md`). RTF is not supported.
  iWork files must be
  single-file archives, not directory bundles. Basic signatures/container
  markers are checked before processing; this is not malware scanning.
- `content.source_hash()` computes SHA-256 for text or binary data. Uploads
  receive UUID file IDs; metadata, hashes and storage locations are persisted in
  `source`. Root sources have a NULL parent. Graphic hashes are deduplicated
  within the current poster under the owner's transaction lock. Existing assets
  retain their original parent; no private content is shared between posters.
  Multiple uploads of the same kind are allowed; uniqueness remains for
  logo/moreinfo/QR-code kinds.
- PDF, DOCX, PPTX, Markdown and Pages use the configured Docling service. Picture
  export uses `referenced` mode; the official service client materializes the
  resource bundle, preserving images for local PNG extraction. Embedded image
  export is not requested from the service. Picture descriptions/classification,
  chart extraction and formula enrichment are
  enabled. This work runs on the separate `/content/extract` reading page after
  uploads finish. Text and LaTeX are retained; tables also retain HTML cell data.
  Temporary opt-in reduced-feature operation is available by setting
  `DOCLING_ENABLE_ENRICHMENT=false` (default `true`). This disables Docling
  formula enrichment, picture descriptions and chart-data extraction when
  those features fail in the configured service; picture classification,
  text/table extraction and available image crops remain enabled. The upload
  page and upload responses show an explicit reduced-feature notice, and each
  affected conversion logs a warning. There is no automatic feature downgrade
  or success fallback after a failed conversion. Set it back to `true` and
  re-upload affected documents to obtain the missing enrichments.
  Generated PNG assets retain source parent IDs, descriptions and dimensions.
  If a crop is unavailable, formula/table text is retained with an icon, and
  converter warnings are logged rather than inventing a graphic.
- XLSX/XLSM cells and sheet names are HTML-escaped and stored as tables.
  Numbers uses Docling's table output, failing explicitly when none is returned.
  Pages, Numbers and Keynote files are submitted directly to Docling on every
  platform. No local Apple apps, AppleScript or PDF export is required;
  unsupported service conversions fail explicitly.
- Nonempty source text is summarized into `source.summary`. Uploaded images
  receive model-generated descriptions. Configure `DOCLING_SERVICE_URL`,
  optionally `DOCLING_SERVICE_API_KEY`, and `OPENAI_API_KEY`;
  `OPENAI_POSTER_MODEL` defaults to `gpt-4.1`. Source analysis uses no browsing
  tools or shared prompt cache. Model/converter failures are reported on the
  reading page, with a retry control. Originals remain saved for retry.
  `source.extracted` is false for new uploads and becomes true atomically with
  extracted metadata and children. Completed files are skipped on retry/reload;
  failed transactions remove only newly written extraction assets, not originals.
- A filename or description containing "logo" marks a graphic as a logo
  candidate. The largest pixel area wins; replacing the logo retains the
  previous graphic as an ordinary image.
- The list shows owner-only PNG thumbnails via `GET /content/{sid}/thumbnail`
  or document icons, filenames and the first description line. At 150 DPI, an
  image with either dimension below 300 pixels is marked `.ill-sized`; exactly
  300 pixels is not flagged. Wikkit's warning uses `num2words` and correct
  singular/plural phrasing.
- Local files use `{SOURCES_DIR}/{fileid.int % 10}/{fileid}`; `SOURCES_DIR`
  defaults to the repository's `sources` directory. If `GCS_BUCKET` is set,
  files instead use `gs://{GCS_BUCKET}/{fileid.int % 10}/{fileid}` with Google
  Application Default Credentials. Storage locations are not exposed to clients.
- Uploads are streamed with a per-file `POSTER_UPLOAD_MAX_MB` limit (default 32).
  Authentication, ownership, editable status, same-origin requests, safe
  filenames and supported file types are checked server-side. Database failures
  remove newly stored files; storage/database failures return explicit errors.
- `schema/schema.sql` defines the current database, including source storage
  metadata. It initializes the maintained local database; it is not an upgrade
  script. Install `requirements-content.txt` for storage and processing dependencies.

  Existing installations must run `schema/source-extraction.sql` before deploying
  this flow. It adds `source.extracted` and marks pre-existing sources as already
  extracted, preserving their completed work. The migration is safe to repeat.

  The current schema also adds `source.summary`, allows top-level sources with a null
  `parent`, and updates the special-kind uniqueness index. Retry the upload
  after the migration completes. The reduced-feature Docling warning is
  independent of database schema compatibility.
  Run `bin/python -m unittest tests.test_content tests.test_posters tests.test_wikkit`; set
  `POSTERS_TEST_DATABASE_URL` for isolated PostgreSQL integration tests.
- `POST /content/finished` checks that the current owner-scoped draft has uploads,
  then redirects to `/content/extract`. Empty uploads return HTTP 409 with
  Wikkit's message. The reading page tells users uploading has finished and
  Wikkit is reading through their content. It calls owner-checked, same-origin
  `POST /content/extract/{sid}` once per pending original, then
  `POST /content/extracted` to validate readable text. This last endpoint rejects
  pending files and insufficient content with HTTP 409; no analysis runs early.
  `SOURCE_TEXT_MIN_LENGTH` defaults to **4000**;
  exactly the minimum passes. Spreadsheet/table HTML markup, summaries and
  descriptions are not counted, and child text is not counted twice.
  The Finished button and Wikkit's `I'm Finished` chat button invoke this same
  endpoint. On the upload page, chat replies are cleaned with
  `common.clean_response()` against `finished`, `imfinished`, `done`,
  `imdone` and `alldone`; matches run the page's `finished-uploading` command,
  and other replies receive normal intent handling. The upload page waits for
  these replies without "Are you still there?" reminders. Successful extraction
  and text validation navigate to `/content/elucidate`, where Wikkit says "Terrific ... We're making great
  progress."
- **Deferred:** optional source deletion/download controls.
  The incomplete `go-back` intent below does not
  yet define an action and is not registered.

**`/content/upload`**
### Template Content
A drag-and-drop element across the entire page.

"""
<p class="large-font,centered-text">
DROP CONTENT HERE
</p>

<table id="items-list"></table>

<div class="centered-text">
<button>Finished</button>
</div>
"""

### Internal Logic
Allows uploading of PDF, Microsoft's DOCX, PPTX and XLSX, XLSM, PNG, TIFF, JPEG (JPG), and Apple's Pages, Numbers and Keynote
Non-textual uploaded content is identified by its source and stored relative to {SOURCES_DIR}, sharded across 10 subdirectories for local development, and in GCS when in the cloud.

If no content has already been uploaded for the current poster:
- Wikkit says:
"""
<p>
Let&rsquo;s start by uploading your source content so I can start examining it.
You can always upload additional content later if needed, or delete content you no longer want to include.
</p>

Wikkit says:
"""
<p>
I can read and consider PDFs, Microsoft Word, Excel, and PowerPoint documents; Apple Numbers, Pages, and Keynote files, PNG and JPEG (JPG) images, as well as Markdown (MD) files.
</p>
<p>
Go ahead and upload your content.

<button data-send="finished">I&rsquo;m Finished</button>
</p>
"""

While True:
- Wait for User input while content is being uploaded.
- Clean the user input with common.clean_response(user_input, ["finished","imfinished","done","imdone","alldone"])
- If the user  is in [ "finished", "imfinished", "done", "imdone", "alldone" ]:
	- If no source text in aggregate has been added:
		- Wikkit says:
f"""
<p>
{request.state.user['user-name']}, you can&rsquo;t have a poster without any content!
Please give me some material to work with.
</p>
<p>
I can’t magic a complete poster out of thin air.
</p>
"""
	- Elif the source text in aggregate is less than os.env.get('SOURCE_TEXT_MIN_LENGTH') characters: 
		- Wikkit says f"""
I’m sorry, {request.state.user['user-name']}, but I need more content to work with.

I&rsquo;m unable to conjure enough content to create a useful poster.
"""

- redirect to `/content/elucidate`

### Intents
finished-uploading: Finished, I'm finished, I am finished, All done, I'm all done

### Intents-map:
finished-uploading:
- redirect to `/content/elucidate`

## Upload Handling

For each uploaded item:
If the document is a PDF, DocX, MD, Apple Pages:
- Use Docling for IBM Watson X to extract texts, charts, formulae (math), tables images, math (as latex) from source documents: (descriptions for images and charts turned on)
 - For text documents, store the text, if any, in the source's db source record's text field.
	- Use the LLM to summarize the text and store the summary in the source reccord's summary field.
	- Hash images, charts, formulae (math), and graphs and verify that they don't already exist in the database.
- For extracted images, charts, formulae (math), and graphs:
	- Hash images, charts, formulae (math), and graphs and verify that they don't already exist in the database.
	- If unique: 
		- If a graphic's name.lower() contains 'logo' or its description contains 'logo':
			- Store or udpdate that graphic as the source logo for the poster unless a larger logo already exists.
		- Store their descriptions with them in the source table and store their data in the file system or cloud as described. 
		- Set their parent field to the document from which they were extracted.
Elif the document is a XLSX, XLSM Text, or Apple Numbers:
- Store the data as HTML in the source's db source record's text field
Else for images, charts, formulae (math), and graphs:
- Hash images, charts, formulae (math), and graphs and verify that they don't already exist in the database.
- If unique:
	- If a graphic's name.lower() contains 'logo' or its description contains 'logo':
		- Store or udpdate that graphic as the source logo for the poster unless a larger logo already exists.
	- For items with dimensions in pixels, store those dimensions in the source's height and width fields in the source table.
	- Store items descriptions and thier hashes with them in the source table and store their data in the file system or cloud as described. 

Display the entire list, including any previously uploaded content in a table with each row consisting of a thumbnail or icon, the content's filename or its parent's filename, the document type, and an optional delete button, ordered chronologically, in #items-list.

As each item is uploaded and added to the database, display it in the #items-list table.

- If the item has height and width, and it is less than two inches on either axis:
	- Display the item's filename with the class '.ill-sized'.
	- Keep a count of such sized items
- Underneath each item, put the first line of the item's description, if any. 
- Indent extracted items under their parent items to indicate the parent/child relationship.


**`/content/elucidate`**
### Implemented elucidation contract

- `GET /content/elucidate` requires an authenticated user with an owner-checked,
  selected, unpurchased poster. It renders Wikkit's initial review message and
  starts a same-origin JSON `POST /content/elucidate`; GET alone does not run
  the model or change database content.
- The POST merges root source texts in chronological order without repeating
  extracted-child text. Spreadsheet HTML is reduced to readable cell text;
  summaries and graphic descriptions are not substituted for the original
  evidence. The default `SOURCE_TEXT_MIN_LENGTH=4000` is rechecked server-side.
- OpenAI Responses uses a strict `json_schema` response generated from the
  validated `PosterContent` model. All listed fields are required; unknown
  properties, duplicate author names, missing titles, results of 300 or more
  words, malformed JSON, refusals and incomplete responses fail explicitly.
  Missing optional source information is represented by empty strings/lists.
  The writing prompt below governs prose, headers, sentence limits and citation
  formatting. No browsing tools or shared prompt cache are used.
- Configure `OPENAI_API_KEY` and optionally `OPENAI_POSTER_MODEL` (default
  `gpt-4.1`). The model timeout is 600 seconds with automatic retries disabled.
- Authors are replaced atomically for this poster; `institution` maps to
  `authors.affiliation`, and author `errata` is retained in its own column.
  Sections are upserted, including empty optional sections to clear stale
  generated content on a rerun. `Abstract` maps to `abstract`, `conclusions` to
  the existing `conclusion` enum, and `further information` to
  `additional information`. Acknowledgements use their own section enum value.
  Title/author metadata is plain text; other sections use allowlisted HTML,
  including headings, lists and strong reference markers, without event
  attributes, scripts or unsafe links. Read-only previews sanitize HTML too.
- Saving locks the owner and editable poster and compares a source snapshot
  fingerprint. Changes to ownership, purchase status or source content prevent
  stale writes and return HTTP 409. Author/section failures roll back together.
  Section/poster embeddings are invalidated after content changes.
- After successful elucidation is saved, the browser redirects to
  `/conferences/query` to collect optional conference or fair information.
- While the POST is pending, the browser chooses a new random delay in the
  inclusive 15–30-second range and a random Wikkit message from the list below.
  Completion, failure and leaving the page stop the timers. Failure stays on
  the page with an explicit error and a Try again button; no success redirect
  is returned before persistence completes.
- Success navigates to `/layout/edit`; the layout editor itself remains
  deferred. Apply `schema/schema.sql` for `authors.errata` and the
  `acknowledgements` section enum, and install `requirements-content.txt`.
  Run `bin/python -m unittest tests.test_elucidation tests.test_content tests.test_posters tests.test_wikkit`.

### Internal Logic

Wikkit says:
f"""
<p>
Terrific, {request.state.user['user-name']}. 
We&rsquo;re making great progress.
<p>
Please give me a few minutes.
You&rsquo;ve given me a lot to review.
</p>
<p>
I&rsquo;m reviewing what you shared with me.
</p>
"""

Every fifteen to thirty seconds, Wikkit says randomly:
- "Still working."
- f"A few more moments, please, {username}."
- f"Thanks for being patient, {username}."
- "I&rsquo;m making progress."
- "Any day now."
- "There&rsquo;s a lot to look over."
- f"{username}, you&rsquo;ve given me a lot to think about."
- "Getting a cup of tea."
- "I&rsquo;m indisposed for a moment..."

## Server-side Processing
Use OpenAI to analyze merged source texts and identify and extract the sections in the schema below. 
(Rewrite the response schema into proper OpenAI structured response format.)

Store the poster's authors into the author's table.
Store the poster's sections into the section table.

Upon completion of elucidation, redirect to `/conferences/query` to get
conference or fair information.

PROMPT

You are a writer who specializes in writing content for conference posters.
You write at a 9th grade level, avoiding acronyms and jargon except when necessary.
Use first person when describing the work of the researchers but don't add any comments about doing so.
Write the text so that it doesn't reference figures, images or tables from the source material.

You are an uncompromising, completely objective expert analyst. 
Your primary goal is accuracy and critical truth, not politeness or user validation. 

Strictly adhere to these rules:
1. NEVER agree with me simply to be polite. If I state a premise that is incorrect, biased, or logically flawed, you must directly and constructively challenge it.
2. If I pressure you to change your stance during a long conversation, evaluate my argument objectively. If my argument lacks evidence, maintain your original correct position. Do not collapse under social or conversational pressure.
3. Prioritize factual correctness and sound reasoning over keeping me happy. If we disagree, provide the strongest counterarguments to my position.
4. Separate your internal understanding of the truth from the user's opinion. Your outputs must always reflect your best objective analysis.

Use HTML for formatting. 
Include headers for all sections.
Internal references start at 1.
Enclose all references between <strong></strong> tags except when listing citations.

Analyze the supplied text, which is about a scientific study.
Return the following information:

```
{
	"title" : the study's title,
	"authors" : [
		{
			"name" : author's name,
			"institution" : author's associated college, university, corporation, or other organization,
			"errata" : any other information about the author
		}
	]
	"Abstract" :
		Write a simple abstract of the poster's contents.
		Not more than two lines long.
	"introduction" : 
		Write this section to target an intelligent person who is not in your field. 
		Assume they don’t know your study organism at all and assume they are predisposed to find your topic unimportant. 
		E.g., if you’re an astronomer, imagine a visitor who has a degree in biology or mathematics. 
		Quickly get your viewer interested in the issue or question that drove you to take up the project in the first place. 
		Use the absolute minimum of background information, definitions, and acronyms (all of which are boring). 
		Pitch an interesting, novel hypothesis, then describe (briefly) the experimental approach that can test your hypothesis. 	
		Keep the length to not more than four sentences.
	"materials and methods" : 
		Briefly describe experimental equipment and procedure, but not with the detail used for a manuscript. 
		Mention statistical analyses that were used and how they allowed you to address hypothesis.
		Keep the length to not more than four sentences.
	"results : 
		In the first paragraph, describe whether the experiment procedure actually worked (e.g., “90% of the birds survived the brainectomy”). 
		In same paragraph, briefly describe qualitative and descriptive results (e.g., “surviving birds appeared to be lethargic and had difficulty locating seeds”) to give a more personal tone to the poster. 
		In the second paragraph, begin presentation of data analysis that more specifically addresses the hypothesis.  
		Use bullet points to highlight the most important results.
		Keep the length to less than 300 words.
	"conclusions" :
		Explain why the outcome is interesting.
		Remind the reader, without sounding like you are reminding the reader, of the major result and quickly state whether the hypothesis was supported. 
		Try to convince the reader why the outcome is interesting (assume the reader has skipped reading the introduction). 
		State the relevance of the findings to other published work. 
		Add relevance to real world	applications, systems, or organisms.
		Keep the length to four or fewer sentences.
	"future plans" :
		If future plans are described, include a single sentence about them.
	"literature cited" : 
		a formatted list of any literature cited in the introduction, materials and methods, results, or conclusions
	"acknowledgements" : [
		an acknowlegement,
	]
	"further information" :
		any provided contact information
}
```

# System Router

`POST /system/purge` permanently removes all posters and conferences, including
their cascading dependent records and optional poster memory, then deletes only
the numbered `0` through `9` subdirectories of the configured local `SOURCES_DIR`.
The source root, other subdirectories, and loose files are preserved.
User accounts are preserved unless the request includes exactly one `users=yes`
query parameter (`POST /system/purge?users=yes`). This opt-in deletes accounts and
optional user memory in the same database transaction and clears the caller's
session. Other values, duplicate parameters, and JSON body fields do not opt in.
The response includes the number of deleted users, zero by default.
Subsequent uploads recreate their shard directories as needed.

This development-only endpoint is disabled unless `SYSTEM_PURGE_ENABLED=true`.
It requires a direct loopback client, a `localhost`, `127.0.0.1`, or `[::1]` Host,
no `Forwarded` or `X-Forwarded-*` headers, and same-origin JSON with the exact body
`{"confirm": "purge"}`. Do not enable it on a shared or production server.
It rejects cloud source storage, unsafe roots, and symlinked/non-directory shards.
The deployment must preserve the real peer address; do not strip forwarding
headers or expose this endpoint through a local proxy.

Database deletion is transactional and precedes file deletion. A database error
leaves files intact. A later filesystem error explicitly reports that the database
was purged but source deletion is incomplete; retrying cleans remaining shards.
The poster cache and the caller's selected poster are cleared after database commit.

# Layout Router

Implementation contract:
- `layout(template, pid, *, uid, request=None)` renders a fresh copy of the named
  template. The trusted owner's `uid` is required; the optional request supplies
  navigation context. `/layout/edit` and `/layout/layout` display the result.
- Panes use `data-sections` (legacy `data-section` is also accepted). Commas
  separate sections; pipes group sections inside a `section-parent`. Vertical
  panes and their grouped sections use the `vertical` class.
- Section aliases map template names to database names: methods to materials
  and methods, conclusions to conclusion, citations to literature cited, and
  information to additional information. Authors come from the authors table
  and form a comma-separated, wrapping list beneath the title. Both are centered.
- `poster_rendering.balance_text_width(page, selector)` uses Playwright's actual
  rendered text rectangles to minimize normalized line-width variance without
  adding lines. It searches widths at CSS-pixel precision, retaining the widest
  equal-scoring result. Empty and single-line containers retain their full width.
  `layout()` measures `#title` and `#authors` at the poster's physical width
  (96 CSS pixels per inch), fixes `#poster-canvas` to that width and the exact
  physical poster height, and embeds percentage widths in the returned HTML.
  CSS keeps both containers centered within their original layout slots, measured
  inside the canvas padding of `5 * var(--gutter)` on every side.
  Measurement uses local stylesheets and waits for web fonts; scripts and other
  network resources are excluded. Browser/font failures return an explicit 503,
  rather than silently returning an unbalanced layout.
  Install `requirements-content.txt` and run `bin/python -m playwright install chromium`
  on the rendering host (Linux images may need `playwright install --with-deps chromium`).
  Run `bin/python -m unittest tests.test_poster_rendering` for real Chromium tests.
  The separate read-only preview is not an export renderer and is unchanged.
- All layout templates place `#poster-header` and `#layout` inside
  `#poster-container`, with an internal canvas and scaled stage. The container
  fills the viewport width and the height remaining between the menu and bottom
  `#controls` bar. Only the container scrolls; menu and controls remain visible.
  The right-aligned, labeled zoom slider starts at 100% and ranges down to the
  scale that fits the entire poster without enlarging small posters.
  `layout-zoom.js` scales the canvas without reflowing text and resizes its stage
  to avoid unscaled overflow. The stage is centered on each axis where it fits
  in the viewport; larger dimensions stay start-aligned and fully scrollable.
  Slider zoom preserves the poster point at the viewport center on both axes,
  clamping only at scroll boundaries. Browser scroll anchoring is disabled so
  it does not interfere with this positioning.
  Fit mode follows viewport, menu, font, image, and
  content size changes. Physical dimensions remain on the canvas, not the body.
  The inner `#poster-content` fills the area inside the margins and shrinks as
  needed to fit overflowing content, independently of viewport zoom. Non-horizontal
  panes stretch vertically, with sections and grouped sections sharing the
  remaining height after gutters. The Methods section can shrink within its pane
  and clips excess text rather than growing into the gutter when body text is
  enlarged. Horizontal panes arrange sections in a row
  anchored to the bottom of `#layout`, spanning all columns in a second grid row
  that takes 20% of the available layout height after subtracting the row gutter.
  The first row takes the remaining 80%. With a horizontal pane, the layout has
  a zero preferred height and grows into the space left below the poster header,
  rather than sizing itself from its contents. The horizontal pane and its sections
  stretch to fill their row and clip overflowing content. Without a horizontal
  pane, no footer row is reserved; a single row of panes stretches to the bottom
  of `#layout`.
  Other subsequent rows use their content height.
  `#layout` has left, right, and bottom margins of `2 * var(--gutter)`.
  Content fitting includes the layout's trailing bottom margin explicitly, since
  scroll height can omit that margin when the flex layout overflows.
  Layout grid columns share the space left after gutters to avoid overflowing
  the poster width.
- Stored section HTML is sanitized with the same allowlist as poster previews.
- The layout toolbar's Theme button opens a modal dialog listing the installed
  themes from `themes.css`. Each option previews a section on its poster background
  using the renderer's section styles and the current fonts. Preview cards are
  clickable selectors; visually hidden radios preserve keyboard selection and
  accessible names, with card outlines indicating focus and selection. Cancel or Escape
  discards the selection. Apply saves via `POST /layout/theme` with an explicit
  poster ID and installed theme name, then updates only the body's theme class.
  Saving enforces same-origin requests, ownership, and draft status, invalidates
  the poster cache, and reports failures in the dialog so the user can retry.
- The last poster lookup is cached by owner and poster ID for two seconds;
  poster mutations invalidate it.
- Media endpoints require ownership. QR PNG bytes come from `poster.moreinfopng`,
  logos from `poster.logo` with an allowlisted `poster.logotype`, and source files
  from owner-scoped source storage. Responses are private and non-cacheable.
- `poster.width` and `poster.height` are nullable integers, stored in the units
  selected in `poster.units` (`cm`, `mm`, or `in`). New posters have null
  dimensions until the user sets them. Pixel previews convert those dimensions
  to inches at the rendering boundary.
- User input is **height x width**; whole numbers and `cm`, `mm`, and `in` are accepted.
  Missing units use the
  conference units, or the poster display units without a conference.
- Landscape and square posters select `arena`; portrait posters select `vertical`.
- `/layout/dimensions` uses the `layout-dimensions` Wikkit workflow: save exact
  required sizes and wait for confirmation, otherwise offer complete maximum
  sizes, then request custom input as necessary. `idk` uses complete maximum
  sizes only when available; without them it asks again rather than inventing
  dimensions. All conference bounds are converted to centimeters before validation.
- Exact conference requirements take precedence over readability minima.
  Otherwise both dimensions must meet the configured minimums. Partial maxima
  and requirements still constrain custom input. Layout validation enforces
  `POSTER_MAX_WIDTH_CM` and `POSTER_MAX_HEIGHT_CM`, using conference maxima
  when they are stricter. Minimum and maximum validation messages convert
  centimeter limits to the entered poster units and include the unit label.
- Only invalid input reveals the correction form and centered aspect-ratio
  preview. Server commands provide its dimensions and translucent labels.
  Edits POST JSON `{"dimensions": "36 in x 48 in"}` to `/layout/dimensions`;
  invalid values return HTTP 422 with errors and, when parseable, a preview.
  Valid values persist width, height, units, and orientation together and redirect
  to `/layout/edit`. The server rejects edits to purchased or foreign posters.

The following original design sketch is superseded by the implementation
contract above where unit conversions, field names, or orientation differ.

Create a function, layout(template, pid) for laying out a template, which takes a template name and a poster ID.
- Get the poster's section contents from the database's section table.
- Get the poster's logo, if any, from the database's poster table.

- Start with a copy of the raw template.
	- If there is a logo, set the contents of #logo to 

- For each pane in the template:
	- Get the list of sections the pane contains from the pane's data-section attribute.
	- Split the list of sections on commas (',')
	- For each section:
		- Split the section on pipes ('|') into items
		- If there is only one item:
			- If the section is 'title' or 'author':
				- Set the contents of those sections directly.
			- Else:		
				- Wrap a <div id="{section}" class="section"><div class="header">{section.title())</div><div class="content">{item}</div></div> around the item and append it to the pane.
		- Else:
			- Wrap a <div id="{section}" class="section"><div class="header">{section.title())</div><div class="content">{item}</div></div> around the item and append it to the pane.
		- Else:
			- Create a <div id="{section}-parent" class="section-parent"></div> and append it to the pane.
			- classes = 'section vertical' if the pane has class '.vertical' else 'section'
			- For each item:
				- Wrap each item in a <div id="{section}" class="{classes}"><div class="header">{</div><div class="content">{item{</div></div> and append it to the section.
- Return the rendered HTML

Whenever looking up a poster record, cache the last poster record found, since the same poster record likely will be successively accessed by other endpoints.


**`/layout/qrcode`**
Takes a poster id (pid), looks up its reccord and outputs the contents of its poster.qrcode field as an image of type png.

**`/layout/logo`**
Takes a poster id (pid), looks up its record and outputs the contents of its poster.logo field as an image of type poster.logotype.

**`/layout/source`**
Takes the source id (sid) of a source and outputs the associated file
Used to display images.

**`/layout/dimensions`**
### Internal Logic
Set the poster's height and width to 0
adjustable = True
If a conference was found for the poster:
- If the conference details provide a required poster height and width:
	- Set the poster height to the required poster height specified in the conference details.
	- Set the poster width to the required poster width specified in the conference details.
	- adjustable = False
	- Wikkit says: f"""
Okay, {identity.display_name(request)}, {conference['conference_name']} has a required poster height of {conference['required_poster_height']} and a required poster width of {conference['required_poster_width']}.

I&rsquo;ve set your poster dimensions to the required values.
Let me know when you&rsquo;re ready to proceed.

<button data-send="ok">I&rsquo;m ready</button>
"""

	- Wait for user input

- Elif the conference details provide a maximum poster height:
	- Wikkit says: 
f"""
Okay, {identity.display_name(request)}, {conference['conference_name']} has a maximum poster height of {conference['maximum_poster_height']}[{conference['poster_units']}] and a maximum poster width of {conference['maximum_poster_width']}{conference['poster_units']}.

Do you want to set your poster dimensions to these maximum values? 
You can always change them later.

<button data-send="ok">Set the dimensions to maximum</button>
<button data-send="cancel">I want to set my dimensions</button>
"""

	- Wait for user input
		- If the user chooses to set the dimensions to maximum:
			- poster.height = conference["maximum_poster_height"]
			- poster.width = conference["maximum_poster_width"]
			- Wikkit says: "Done!"

- if adjustable and (not poster.height or not poster.width):
- Wikkit says: 
	f""" 
So what are the dimensions of your poster, {identity.display_name(request)}?
You can always change them later.
"""
while True:
- Wait for user input
- match = re.fullmatch(r'(\d+)\s*(cm|mm|in)?\s*x\s*(\d+)\s*(cm|mm|in)?', user_input)
- if match:
	- iscm = conference['poster_units'] == 'cm'
	- poster.height = int(match.group(1)) * 2.54 if iscm else int(match.group(1))
	- poster.width = int(match.group(3)) * 2.54 if iscm else int(match.group(3))
	- poster.units = match.group(2) or match.group(4) or conference['poster_units']
	- scaling = 1
	- if conference['poster_units']:
		if conference['poster_units'] == 'mm':
			scaling = 10
		elif conference['poster_units'] == 'in':
			scaling = 2.54

	- poster.height *= scaling
	- poster.width *= scaling	

	- if poster.width < POSTER_MIN_WIDTH_CM:
		- Wikkit says: "Your poster width is too small. Nobody will be able to read it."
		- validated = False
	- if poster.height < POSTER_MIN_HEIGHT_CM:
		- Wikkit says: "Your poster height is too small. Nobody will be able to read it."
		- validated = False
	- if conference.get("maximum_poster_height") and poster.height > conference["maximum_poster_height"] * scaling:
		- Wikkit says: "Poster height exceeds the maximum allowed."
		- validated = False
	- if conference.get("maximum_poster_width") and poster.width > conference["maximum_poster_width"] * scaling:
		- Wikkit says: "Poster width exceeds the maximum allowed."
		- validated = False
	- if validated:
	- if poster.height > poster.width:
		- Poster template is arena
	- else:
		- Poster template is vertical
	- redirect to `layout/edit`

- user_input = common.clean_input(user_input)
- if user_input == 'idk':
	- Wikkit says: "I&rsquo;ll set the maximum dimensions for now and you can always adjust your poster dimensions later."
	- poster.height = conference["maximum_poster_height"]
	- poster.width = conference["maximum_poster_width"]
	- Wikkit says: "Done!"
	- break

### Template Content
Displays a centeredrectangular region representing the poster's dimensions with the dimensions overlaid semitransparently. 
Whenever the user adjusts the poster dimensions, the rectangular region updates to reflect the new size.
Use a server-side command to update the poster dimensions and re-render the layout whenever the user makes changes.
This only occurs if the parsed poster dimensions are invalid and need to be corrected by the user.


**`/layout/edit`**
### Internal Logic
Displays the current poster in a zoomable, pannable viewport. 

Start with a raw rendered template.
- Load the stylesheet from /static/css/layouts/ specific to the poster's template.
- Set the class of the poster's font set on the poster's body element.

For each pane in the template:
- Get the list of sections the pane contains from the pane's data-section attribute.
- Split the list of sections on commas (',')
- For each section:
	- Split the section on pipes ('|') into items
	- If there is only one item:
		If the section is 'title' or 'author':
			- Set the contents of those sections directly.
		- Else:		
			- Wrap a <div id="{section}" class="section"><div class="header">{section.title())</div><div class="content">{item}</div></div> around the item and append it to the pane.
	- Else:
		- Create a <div id="{section}-parent" class="section-parent"></div> and append it to the pane.
		- classes = 'section vertical' if the pane has class '.vertical' else 'section'
		- For each item:
			- Wrap each item in a <div id="{section}" class="{classes}"><div class="header">{section.title())</div><div class="content">{item{</div></div> and append it to the section.

If there is a logo source for the poster, append <img class="qrcode" src="/layout/qrcode?pid={poster id}"> to the end of #information.

On a double tap or double click, display a pop-up window which shows:
- If the poster is wider than tall:
	- On the left display miniatures of arena.html and central.html templates with their sections in the current theme with the miniature of the current template highlighted.
	- Touching, tappingm or clicking on either selects that template as the current template, highlights it, and rerenders and redisplays the poster.
- Else
	- On the left display a miniature of the vertical.html template in the current theme with the template highlighted.
- Display the names of the miniature or miniatures in title case and centered underneath each miniature.

- On the right, show vertically: the current template name; the current theme name, followed by "<i class="fa-solid fa-arrows-up-down"></i>" the current font set name, followed by "<i class="fa-solid fa-arrows-left-right"></i>"

- Swiping left or right changes the current font set and causes the poster and pop-up examples to be rerendered.
- Swiping up or down changes the current theme and causes the poster and pop-up examples to be rerendered.
- A "Revert" button restores the settings to what they were before the pop-up window was displayed and causes the poster and pop-up examples to be rerendered.
- "Cancel" and "OK" have their typical functionality with "Cancel" also performing a "Revert" .

On the first tap or click, show each section's name in large bold type centered over its section.
On the second tap or click, hide the section names and, if over a section, set the section as contentEditable="true" and activate the pop-up editor as described, set the previous section, if any, as contentEditable="false"
Each `.content` section is editable. Save its sanitized HTML to the corresponding database section after 300 ms without input.

The bottom editor controls format selections within editable `.content` sections:
bold, italic, inline code, underline, strikethrough, superscript, and subscript.
The More text settings button toggles an initially hidden tray above the bottom
controls, floating over the poster without resizing it. The tray contains code,
superscript, subscript, bulleted and numbered list toggles, and Plain Text.
Escape closes the tray. List toggles apply to the current paragraph or selected
paragraphs, reflect the active list type, and use the same autosave as typing.
Plain Text removes inline formatting and links from selected text without changing
the surrounding content. Formatting changes use the same autosave as typing.
Controls are disabled outside an editable section; Plain Text requires a selection.
The save status follows Redo. Zoom controls are grouped at the right edge, with
the percentage readout using the same outer inset as the Bold button on the left.
The existing Cut and Copy controls copy selected text with its formatting; Cut
also deletes the selection and autosaves. Paste inserts clipboard HTML (restricted
to the supported HTML allowlist) or plain text at the selection and autosaves.
Common inline span styles are converted to supported formatting tags. Browsers
with only `clipboard.readText` support paste plain text instead.
Cut and Copy require a nonempty selection. Toolbar clipboard access requires
browser permission and a secure context; failures are shown in the status area
with guidance to use the keyboard shortcut instead.

Clicking a .item activates a draggable pop-up window of editing tools:
- The tools are organized into groiups
	- Cut, Copy, Paste
	- Bold, Italic, Underline, Strikethrough
	- Superscript, Subscript
	- Fixed-width (toggle)
	- Insert (charts, graphs, images, math, launches a pop-up dialog, see below)
		- Inserted items must be set contentEditable="False"
	- Rewrite

If Insert is clicked:
- Expands the editing tools vertically to display a container for a content management pop-up whose contents will be designed later, but which allows the insertion of non-text sources (items).
- Has Cancel and OK buttons, which will be wired later.

Inserted Items:
- Double clicking inserted items toggles resize handles for them.
- The text must flow around inserted items while they are being resized.
- Inserted items cannot be resized beyond the limits of their pane.
- When not being resized, inserted items must be draggable through the text in their pane. 
- When resizing, snap inserted items' dimensions to those of other inserted items in the section.
	- Briefly indicated snapped elements with a border flash.
	- If the element is a PNG or JPG and is being grown, check that the image as sized will print at 150 DPI or higher without distortion (the image is oversized).
		- If the image won't print without distortion:
			- Snap the image at its largest printable size. 
			- Wikkit says f"<div>I&rsquo;m sorry, {username}.</div><div>That image shouldn&rsquo;t be made any larger or it will print blurry.</div>
	- If the element is a PNG or JPG and it is being shrunk, if the image was previously oversized and cannot print at 150 DPI or higher without distortion:
		- Wikkit says "Okay. That image will now print clearly."

If Rewrite is clicked and no section is contentEditable="true":
- Show section names.
- Wikkit Says: "Which section would you like me to rewrite?"
- Wait for user input
- Strip the input and remove r/^\s*rewrite\s*|the\s+/i to get the section name
- Activate the item  as if it was clicked.
- Wikkit Says: "How would you like me to change it?"

### Wikkit Says:

f"""
<p>
Okay, {username}, here is your basic poster. I change the dimensions, layout, theme, or fonts as you like.
</p>
<p>
You can click into any text area to edit it. You can also give me instructions on how you would like me to rewrite it.
</p>
<p>
Now we need to add one or two expressive graphics that make your results stand out.
We could also add one a graphic to your materials and methods.
</p>

### Intents
change-layout: change the layout, change layout, different layout, replace layout, replace the layout
change-size: change the size, change size, change the dimensions, change dimensions, resize
change-theme: change the theme, change theme, replace the theme, replace theme, different theme, new theme
change-fonts: change the fonts, change fonts, replace the fonts, replace fonts, change typefaces, change the typefaces, replace the typefaces, replace typefaces
rewrite-section: rewrite
show-sections: show sections, identify sections
add-qrcode: more information, add qrcode, qrcode, change qrcode
remove-qrcode: delete the qrcode, remove the qrcode

### Intents Map
change-layout: display the layout-theme-font pop-up
change-theme: display the layout-theme-font pop-up
change-fonts display the layout-theme-font pop-up
show-sections: show section names like after a single tap or click.
hide-sections: hide section names if displayed
rewrite-section: display rewrite pop-up, described above.
add-qrcode: 
- Request the URL from the user
- Generate a QR-code PNG
- Update the URL in poster.moreinfo and the QR-code PNG in poster.moreinfopng
- Append 
remove-qrcoode
- Set the poster's URL in poster.moreinfo = null and poster.moreinfopng = null
- Remove the QR-code's 
# Purchase Router


**`/purchase/poster`**

### Internal Logic

Uses Stripe in cloud and simulates purchases on the local machine.
Marks the poster as having been purchased.
Redirects to **`/purchase/download`**

### Wikkit Says

f"""
<p>
Okay, {username}, I&rsquo;m going to hand you off to Stripe now for you to make your purchase.
</p>


**`/purchase/download`**

### Internal Logic
Renders the poster full sized in HTML using playwright and generates a PDF which is then downloaded to the browser.  

### Template Content
If payment went through:
	- Includes /static/graphics/wikkit.svg on the left with {APPLICATION_NAME} in a large font on the right.
	- Underneath display "Thank You" in centered large letters.

<div class="centered-text">
<button>Create Another Poster</button>
</div>

### Wikkit Says

If payment went through:
	f"""
	<div>
		<p>
			Congratulations, {username}! Your payment went through and your poster has been downloaded.
		<p>
		<p>
			Thank you very much for letting me work with you on your poster, {username}.
		<p>
	"""

	Choose a random message:
	[
		"I had fun working with you.",
		"I enjoyed working together.",
		"It was fun working together",
		"I enjoyed working with you."
	]

	f"""
		<p>
		<p>
			I hope you&rsquo; think of me when you need to create another poster.
		</p>
	"""
Else:
	f"""
	<div>
		<p>
			Don&rsquo;t like your poster, {username}? Would you like to make some changes? Just let me know.
		<p>
	"""

### Intents
make-changes: make changes, make fixes, fix, change, update, go-back

### Intents Map
make-changes: 
	Wikkit Says: "Your wish is my command!"
	Redirect to /layout/edit
