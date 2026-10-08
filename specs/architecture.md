# Introduction

We are creating a new SaaS FastAPI application to create conference and fair presentation posters and allow their creators to print them and share them with the public.
The application is configured as a Docker container and runs under Docker on the local machine and in Google Cloud Run.

# Data Storage

The database is PostgreSQL equipped with PGVector. 
On-going chats are stored in Postgres to allow review and continuation between sessions.
Sessions are managed with Redis (locally installed).

# Session Management

Sessions persist for {SESSION_MAX_AGE}.
Users are notified of incipient session expiration at {SESSION_EXPIRATION_NOTIFICATION=480}.
When a session is terminated, the session's associated chat websocket must also be closed with an appropriate message.

# Cookie Identity

A cookie is used to keep track of the last user and welcome them back by name when they return.

# Payment Processing 

Uses Stripe in cloud and simulates purchases on the local machine.

# Poster Management

Posters are either unpurchased or purchased. 
Unpurchased posters cannot be downloaded.
Purchased posters can be downloaded as digitally native PDFs.
A user can have only one unpurchased poster at a time, but any number of purchased posters.

# Schema

A preliminary schema is in `schema/schema.sql`. It needs rewriting.

In schema.sql, modify the source and item tables so that conferences suppor full-text search, along with embedding.
See search.md for an explanation of search goals and implementation guidelines.

# Intents
## General Intents

There is a collection of standard intents and a map of intents to handler functions.

### Default Intents: 
- sign-out: sign out, sign out, log off, logout
- sign-up: register, create an account, sign up, sign-up, make a poster
- sign-in: sign in, log in, login, log-in
- product-pricing: pricing, what does it cost, is this free
- reset-password: reset password, change password, lost password
- purchase: buy
- delete: remove, delete, discard
- create poster: create poster
- delete poster: delete poster
- download: downloads
- help: help
- undo: undo
- redo: redo
- okay: okay

### Intent Map:
- sign-up: lambda redirect to `/account/register`
- sign-in: lambda redirect to `/acccount/signin`
- sign-out: lambda redirect to `/account/signout`
- product-pricing: lambda redirect to `/wikkit/pricing`
- reset-password: lambda redirect to `/account/reset`
- help: lambda redirect to `/wikkit/help`
- create poster: lambda redirect to /poster/create


## Endpoint Intents

Selected endpoints have a collection of page-specific intents and/or a map of intents to handler functions in the router that contains the endpoint. Page-specific intent handler map function specifications, which may be None, supersede like-named global intent map functions.

## Intent Processing

For any given input, use openai.dispatch_intent to identify any intent in the message.
If there is an intent in the user's input and the current intent map has a handler function for the intent, then invoke the handler function for the intent, passing the user's message to the handler as a parameter. Otherwise, pass the message back to the client with intent information as a JSON data structure.

# Router: Wikkit the Wizard

**`/wikkit`**
Provides the basic structural chatbot interface HTML.
The chatbot name is "Wikkit".
Contains a draggable, resizable, framed div (#chatbot) with an adjustable scrollable region to record the chat and a fixed-height, basic contentEditable region at the bottom of #chat for user input.
Underneath the chatbot div is a viewport-sized iframe (100% width, 100% height).

User input messages are moved from the contentEditable region to the bottom of the scrollable region before being sent to the server over the websocket.
The page also supports a websocket connection to the chatbot.
Accepts a URL as a post parameter to load; if no URL is provided then loads `/wikkit/magic` by default.

Write any client-side code for managing the chatbot into /status/javascript/wikkit.js
Each page loaded into the iframe must load wikkit.js. 

When a page is loaded into the iframe, the page must call a wikkit.js function, `connect(listen)`, which is bound to the iframe.
The listen parameter is a callback that delivers chatbot messages to the client page.
`connect(listen)` returns a JavaScript object, `apiobj`, which should probably be a singleton, that exposes two methods to the client:

1 **`apiobj.say(html)`**
Pass a text or an HTML fragement to `say()` to have it added as a chatbot message to the end of the #chatbot div message stream (above the contentEditable region)

2 **`apiobj.command(html, echo=false)`**
Pass text directly to the server via the websocket.
If echo is true then also call `apiobj.say(html)`.

And see `Wikkit.md`

# Home Page

**`/`** 
Redirects to `/wikkit`

# Wikkit Router

## Main Landing Page

**`/wikkit/magic`**
### Internal Logic
If the user is returning (as indicated by the presence of the user cookie):
    redirect to `/returning`

### Template Content
Displays a landing page.
Shows `/static/graphics/wikkit.svg` on the left with {APPLICATION_NAME} in a large font on the right.

### Wikkit says:
f"""
<div>
Welcome to {APPLICATION_NAME}. I&rsquo;m {APPLICATION_NAME}&rsquo;s resident wizard.
</div>
<div>
With a few minutes and a little magic, we can quickly and easily prepare your conference or fair poster.
</div>
<p>
Can I have your name to start?
</p>
"""

Wait for the user to respond.
Strip any whitespace from the form.
Change any run of whitespace to a single space.
Strip any non-alphanumeric characters except spaces.

## Returning User Landing Page

**`/wikkit/returning`**
### Internal Logic
Set session.username to cookie.username

### Template Content
Display a welcome back to {cookie.username}.

"Please sign-in to continue working on your poster."

Includes /static/graphics/wikkit.svg on the left with {APPLICATION_NAME} in a large font on the right.

### Wikkit says:

f"""
<p>
Welcome back, {} to {APPLICATION_NAME}! 
I&*rsquo;m happy to see you again.
</p>
<p>
Ready to get back to work on your poster?
</p>
"""

## Service Pricing Breakdown

**`/wikkit/pricing`**
### Internal Logic

(None)

### Template Content 
Placeholder content about posters being paid for only when ready for printing.

### Wikkit says:

f"""
<p>
Here is the pricing information. Is there anything else I can tell you?
</p>
"""

## Help Page 

**`/wikkit/help`**
### Internal Logic

(None)

### Template Content

What can I help you with?

- Link to account page.
- Link to password reset.

### Wikkit says:

f"""
<p>
What  can I help you with?
</p>
"""

# Account Router

## Account Registration Page

**`/acccount/register`**
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
Congratulations, {username}. 
We&rsquo;re all set.
</p>
<p>
Let&rsquo;s get started on your poster!"
</p>

## Account Sign-in

**`/account/signin`**
Allows the user to sign into an existing account.
Provides a link to `/account/reset` to allow the user to reset their password.

### Wikkit Says:

f"""
<p>
"Please sign in to {APPLICATION_NAME}.
</p>
"""

## Account Sign-out

**`/account/signout`**
Sign out of an active account.
Shows a page telling the user that they've been signed out of {APPLICATION_NAME}.
Provides a link back to `/home`.

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
Send a recovery link to the user's email address.

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
{username}, please provide your email address to reset your password.
</p>
# Posters Router

**`/posters/list`**
### Internal Logic
Shows a list of the posters the user has created.
The list is sortable by poster name, conference date and supports the semantic search logic described in search.md
The list can be displayed as a vertically scrolling list or an array of large thumbnails.
Posters can be optionally deleted with confirmation.

**`/posters/create`**

@@@@

**`/poster/delete`**
### Internal Logic
Deletes the user's designated poster

### Template Content
(None)

### Wikkit Says
[Nothing]


# Conferences Router

**`/conferences/find`**
Given a set of criteria describing a conference or fair, including its name, location, purpose, date or dates use conferences.get_conference_info() to find matching conferences

**`/conferences/query`**

### Wikkit says:

f"""
<div>Let me begin by asking what you know about the conference or fair for which we're creating your poster.</div<>>
Please tell me any information you have about your conference or fair, such as its name, subject area, location, or dates: 
"""

Wait for user input.

use `/conferences/find` to find any matching conferences.
Display any found conferences or fairs on the page in an enumerated, clickable scrollable list searchable by name.
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
{username}, please select the {scope} that best matches your poster requirements by clicking on it, entering the corresponding number, or telling me its name.
If there is no matching {scope}, just let me know and we&rsquo;ll move on.
</p>
<p>
If none of these are correct, just let me to continue without conference information.
</p>
Enter the number or the name of the {scope}
"""

If 

### Intents




# Content Router

**`/content/upload`**
### Internal Logic
Allows uploading of PDF, Microsoft's DOCX, PPTX and XLSX, PNG, TIFF, JPEG (JPG), and Apple's Pages, Numbers and Keynote
Uploaded content is identified by its source and stored relative to {SOURCES_DIR}, sharded across 10 subdirectories for local development, and in GCS when in the cloud.

If the document is a PDF, DocX, Text, MD, RTF, Apple Pages:
    Use Docling to extract texts, images, math (as latex) from source documents: (descriptions for images and charts turned on)
        Store the consolidated text, if any, in the source's db source record's text field.
        Store any extracted elements in the item table.
        For images, charts and graphs, store their descriptions with them in the item table.
Else
    Use Docling to describe the image, chart or graph.
    Store the item's description in the item table.


**`/content/elucidate`**
### Internal Logic
Uses OpenAI to analyze merged source texts and identify and extract the sections in the schema below. 
(Rewrite the response schema into proper OpenAI structured response format.)

Store the poster's authors into the author's table.
Store the poster's sections into section table.

PROMPT
You are a writer who specializes in writing content for conference posters.
You write at a 9th grade level, avoiding acronyms and jargon except when necessary.
Use first person when describing the work of the researchers but don't add any comments about doing so.
Write the text so that it doesn't reference figures, images or tables from the source material.

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

Upon completion of elucidation, redirect to `/content/manage`.


**`/content/manage`**

### Internal Logic

Displays the poster's content in section order, in a scrollable list. 
Each section is labeled followed by a content-editable dynamically sizable block that holds the section's text
An "Update" button writes any changes to the server.





# Layout, Size and Theme Router

**`/layout/`**


# Purchase Router

**`/purchase/poster`**

### Internal Logic

Uses Stripe in cloud and simulates purchases on the local machine.

**`/purchase/download`**





Extra content
    Recommendations
    Tools to help


/conferences
    /search (post)
       receives: criteria, string
        useGetConferenceInfo() in wikkit.py
        returns list of conferences 

    /select

