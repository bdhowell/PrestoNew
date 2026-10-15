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

# Schema

A preliminary schema is in `schema/schema.sql`. It needs rewriting.

In schema.sql, modify the source and item tables so that conferences suppor full-text search, along with embedding.
See search.md for an explanation of search goals and implementation guidelines.
 
# Client-side functions

1 **`apiobj.say(html)`**
Pass a text or an HTML fragement to `say()` to have it wrapped in <div class="chat"></div> tags and added as a chatbot message to the end of the #chatbot div message stream (above the contentEditable region).

2 **`apiobj.command(html, echo=false)`**
Pass text directly to the server via the websocket.
If echo is true then also call `apiobj.say(html)`.

# Routers and Endpoints

Place each router in its own file with any local-to-it functions

Place any router-specific javascript in /static/javascript/{router}/router.js
Place any page-specific javascript in /static/{router}/{page}.js

Place any router-specific CSS in /static/javascript/{router}/router.css
Place any page-specific CSS in /static/{router}/{page}.css

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
Create a common function that checks whether the user has an existing unpurchased poster.
- If it does: 
	- Have Wikkit say f"I&rsquo;m sorry, {username}, you can work on only one poster at a time.
- Else:
	- Create a new unpurchased poster.
	- Have Wikkit say f"I&rsquo'm creating your new poster, {username}!"
	- Redirect to `/posters/create/`


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
	- Have Wikkit say f"I&rsquo;m sorry, {username}, you can work on only one poster at a time. Would you like me to open it for you?"

- Else:
	- Create a new unpurchased poster.
	- Have Wikkit say f"I&rsquo'm creating your new poster, {username}!"
	- Redirect to `/posters/create`

**`/posters/create`**
### Internal Logic
If the user already has an unpurchased poster:
- Have Wikkit say f"I&rsquo'm sorry, {username}! You already have an existing unpurchased poster. Would you "
- Redirect to `/layout/edit`

If the poster's template has not been set:
	- If the poster's aspect ratio is vertical, then set the template as /templates/layout/vertical.html
		- Else randomly set the template as /templates/layout/arena.html or /templates/layout/central.html

	- If the poster's theme is unset, then randomly set its theme to one of the themes in themes.css

	- If the 

Randomly
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

### Wikkit says: (client output)

f"""
<div>Let me begin by asking what you know about the conference or fair for which we're creating your poster.</div<>>
Please tell me any information you have about your conference or fair, such as its name, subject area, location, or dates: 
"""

Wait for user input.

If there is no user input or the user replied t then:
- Wikkit says "That

Use `/conferences/find` to find any matching conferences, passing the user input as the search criteria.
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
{username}, please select the {scope} that best matches your poster requirements by clicking on it, entering its corresponding number, or telling me its name.
If there is no matching {scope}, just let me know and we&rsquo;ll move on.
</p>
<p>
If none of these are correct, just let me to continue without conference information.
</p>
Enter the number or the name of the {scope}
"""

If @@@@@

### Intents
@@@@


# Content Router

Write a local function that takes a Python text or binary object and produces a hash from it.

**`/content/upload`**
### Internal Logic
Allows uploading of PDF, Microsoft's DOCX, PPTX and XLSX, XLSM, PNG, TIFF, JPEG (JPG), and Apple's Pages, Numbers and Keynote
Non-textual uploaded content is identified by its source and stored relative to {SOURCES_DIR}, sharded across 10 subdirectories for local development, and in GCS when in the cloud.

If the document is a PDF, DocX, MD, RTF, Apple Pages:
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

As each item is uploaded and added to the database, display the entire list, including any previously uploaded content in a table with each row consisting of a thumbnail or icon, the content's filename or its parent's filename, the document type, and an optional delete button, ordered chronologically, in #items-list.

- If the item has height and width, and it is less than two inches on either axis:
	- Display the item's filename with the class '.ill-sized'.
	- Keep a count of such sized items
- Underneath each item, put the first line of the item's description, if any. 
- Indent extracted items under their parent items to indicate the parent/child relationship.

If the count of odd-sized items:
- morethan = count > 1
- Wikkit says: f"""
	<div>
	{use num2words} file{'s are' if morethan else 'is'} will likely not be easily visible from typical poster viewing distances and I have no spell that could change that.
	</div>
	<div>
	I&rsquo;ve identified {'them' if morethan else 'it'} by coloring {'their' if morethan else 'its'} <span class="ill-sized">filename{'s' if morethan else ''}</span>.
	</div>

When the Finished button is pressed:
- If no content has been added:
	Wikkit says f"{username}, you can&rsquo;t have a poster without any content!"
- Elif there is no source text in aggregate or the aggregated source text is less than os.env.get('SOURCE_TEXT_MIN_LENGTH') characters:
	Wikkit says f"""
		<p>
		I&rsquo;m sorry, {username}, but I need more content to work with.
		</p>
		<p>
		Even I can&rsquo;t conjure research out of thin air.
		</p>
	"""
- Else:
	redirect to `/content/elucidate`

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

### Wikkit Says

"""
<p>
Upload raw content for your poster. 
</p>
<p>
This can include PDFs, Microsoft Word, PowerPoint, and Excel documents, Apple Pages, Keynote, and Numbers documents, and PNG, TIFF, JPG (JPEG) images.
</p>
<p>
Click the <strong>Finish</strong> button or let me know when you have finished uploading. 
</p>
"""

### Intents
finished-uploading: Finished, I'm finished, I am finished, All done, I'm all done
- Wikkit says "Moving on..."
go-back: 
- Wikkit says "

### Intents-map:
finished-uploading:
- Wikkit says f"Terrific, {username}. We&rsquo;re making great progress."
- redirect to `/content/elucidate`


**`/content/elucidate`**
### Internal Logic
Uses OpenAI to analyze merged source texts and identify and extract the sections in the schema below. 
(Rewrite the response schema into proper OpenAI structured response format.)

Store the poster's authors into the author's table.
Store the poster's sections into the section table.

Upon completion of elucidation, redirect to `/layout/edit`.

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

### Wikkit Says

f"""
<p>
Please give me a few moments,{username}.
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


# Layout Router

Create a function, layout(template, pid)) for laying out a template, which takes a template name and a poster ID.
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
- Display a container for a content management pop-up whose contents will be designed later, but which allows the insertion of non-text sources (items).
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
	- Activate the item  as if it was cl

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
