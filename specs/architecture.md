# Introduction

We are creating a new SaaS FastAPI application to create conference and fair presentation posters and allow their creators to print them and share them with the public.
The application is configured as a Docker container and runs under Docker on the local machine and in Google Cloud Run.

# Data Storage

The database is PostgreSQL equipped with PGVector. 
On-going chats are stored in Postgres to allow review and continuation between sessions.
Sessions are managed with Redis (installed).

## Session Management

Sessions persist for {SESSION_MAX_AGE}.
Users are notified of incipient session expiration at {SESSION_EXPIRATION_NOTIFICATION=480}.
When a session is terminated, the session's associated chat must also be 

## Cookie Identity

A cookie is used to keep track of the last user and welcome them back by name when they return.

# Chat Management

A preliminary schema is in schema/schema.sql.

Posters are either unpurchased or purchased. 
Unpurchased posters cannot be downloaded.
Purchased posters can be downloaded as digitally native PDFs.

In schema.sql, modify the source and item tables so the suppor full-text search, along with embedding.
See search.md for an explanation of search goals and implementation guidelines.

## Endpoint Intents

Each endpoint has a collection of accepted intents and a map of intents to handler functions in the blueprint that contains it.

## Blueprint: Wikkit the Wizard

**`/wikkit`**
Provides the basic structural chatbot interface HTML.
The chatbot name is "Wikkit".
Contains a draggable, resizable, framed div (#chatbot) with an adjustable scrollable region to record the chat and a fixed-height, basic contentEditable region at the bottom of #chat for user input.
Underneath the chatbot div is a viewport-sized iframe (100% width, 100% height).

User input messages are moved from the contentEditable region to the bottom of the scrollable region before being sent to the server over the websocket.
The page also supports a websocket connection to the chatbot.
Accepts a URL as a post parameter to load; if no URL is provided then loads /home by default.

Write any client-side code for managing the chatbot into /status/javascript/wikkit.js
Each page loaded into the iframe must load wikkit.js. 

When a page is loaded into the iframe, the page must call a wikkit.js function, `connect(listen)`, which is bound to the iframe.
The listen parameter is a callback that delivers chatbot messages to the client page.
`connect(listen)` returns a JavaScript object, `apiobj`, which should probably be a singleton, that exposes two methods to the client:

1 **`apiobj.say(html)`**
Pass a text or an HTML fragement to `say()` to have it added as a chatbot emssage to the end of the #chatbot div message stream (above the contentEditable region)

2 **`apiobj.command(html, echo=false)`**
Pass text directly to the server via the websocket.
If echo is true then also call `apiobj.say(html)`.

And see Wikkit.md

### Default Intents: 
- sign-out: sign out, sign out, log off, logout
- sign-up: register, create an account, sign up, sign-up, make a poster
- sign-in: sign in, log in, login, log-in
- product_pricing: pricing, what does it cost, is this free
- help: help

### Intent Map:
- sign-up: lambda redirect to `/account/register`
- sign-in: lambda redirect to `/acccount/signin`
- sign-out: lambda redirect to `/account/signout`
- product_pricing: lambda redirect to `/main/pricing`

## home page

**`/`** 
Redirectss to `/wikkit`

## Wikkit Blueprint

**`/wikkit/magic`**
If the user is returning (as indicated by the presence of the user cookie):
    redirect to `/returning`

Displays a landing page.
On load, the landing page displays /static/graphics/wikkit.svg on the left with {APPLICATION_NAME} in a large font on the right.

Wikkit says:

f"""
<div>
Welcome to {APPLICATION_NAME}. I&rsquo;m {APPLICATION_NAME}&rsquo;s resident wizard.
</div>
<div>
With a few minutes and a little magic, we can quickly and easily prepare your conference or fair poster.
</div>
<p>
Please create an account to begin.
</p>
"""

**`/wikkit/returning`**


f"""
<p>
Welcome back to {APPLICATION_NAME}! 
I&*rsquo;m happy to see you again.
</p>
<p>
Ready to get back to work on your poster?
</p>
"""

Otherwise, 

## Account Blueprint

**`/acccount/register`**
Allows a user to create an account.
Requires a username, password, email address.
Sends a confirmation email using local SMPT support or SMTP2Go when running in the cloud (*Need to register*)

Upon completion of registration, send confirmation 

**`/account/signin`**
Allows the user to sign into an existing account.
Provides a link to `/account/reset` to allow the user to reset their password.

f"""
<p>
Wikkit says "Please sign in to {APPLICATION_NAME}.
</p>
"""

**`/account/signout`**
Sign out of an active account.
Shows a page telling the user that they've been signed out of {APPLICATION_NAME}.
Provides a link back to `/home`.

**`/account/reset`**
Reset user's password feature.

**`/account/update`**
change password
delete acccount

## Posters Blueprint

**`/posters/list`**
Shows a list of the posters the user has created.
The list is sortable by poster name, conference date and supports the semantic search logic described in search.md
The list can be displayed as a vertically scrolling list or an array of large thumbnails.

**`posters/create `**. 

## conferences blueprint

**`/conferences/find`**
Given a set of criteria describing a conference or fair, including its name, location, purpose, date or dates use conferences.get_conference_info() to find matching conferences

**`/conferences/query`**
The chatbot says
f"""
<div>Let me begin by asking what you know about the conference or fair for which we're creating your poster.</div<>>
Please tell me any information you have about your conference or fair, such as its name, subject area, location, or dates: 
"""

Wait for user input.

use `/conferences/find` to find any matching conferences.
Display any found conferences on the page in an enumerated scrollable list searchable by name.
 
{username}, please select the {scope} that best matches your poster requirements by entering the corresponding number.[/bright_yellow]")
print("> [bright_yellow]If none of these are correct, you can enter '0' to continue without conference information.[/bright_yellow]")
print(f"\n> [bright_yellow]Enter the number of the {scope}: [/bright_yellow]", end="")


    return username

## Content Bluepriint

**`/content/upload`**
Allows uploading of PDF, Microsoft's DOCX, PPTX and XLSX, PNG, TIFF, JPEG (JPG), and Apple's Pages, Numbers and Keynote
Uploaded content is identified by its source and stored relative to {SOURCES_DIR}, sharded across 10 subdirectories for local development, and in GCS when in the cloud.

If the document is a PDF, DocX, Text, MD, RTF, Apple Pages:
    Use Docling to extract texts, images, math (as latex) from source documents: (descriptions for images and charts turned on)
        Store the consolidated text, if any, in the source's db source record's text field.
        Store any extracted elements in the extract table.
        For images, charts and graphs, store their descriptions with them in the extract table.
Else
    Use Docling to describe the image, chart or graph
    Store the item's description in the 

After content has been uploaded and extracted or analyzed,


Layout, Size and Theme Selection






Extra content
    Recommendations
    Tools to help


/conferences
    /search (post)
       receives: criteria, string
        useGetConferenceInfo() in wikkit.py
        returns list of conferences 

    /select


Print Your Poster