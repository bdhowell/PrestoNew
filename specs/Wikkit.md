# Chatbot

We are going to build a chatbot that will help users create print-ready conference posters adding on to the code, logic, and data structures we already have in the repository.

The chatbot utilizes the /wikkit endpoint to load /wikkit/welcome into the endpoint's iframe and provide access to /wikkit's overlayed #wikkit div, which is for chatbot interactions with the user while overlaying the active page in the iframe.

The chatbot will be built using LangChain and LangGraph interacting with the main application's webserver, app.py.
Each chat node has its own webpage associated with it, which is loaded into the iframe, underneath the chatbot div, whenever the node is accessed.

Modify getConferenceInfo.p in wikkit.py y to accept or use the main application server's openAI connection information, rather than requiring settings in .

Each poster will have one chat associated with it, which must persist alongside the poster.
Deletion of a poster, or of the poster's creater, must delete the chat.

## Intent Classification

Use best choice or bag-of-words for intent classification.

## Checkpoints and Long-term store

Use the existing Postgres database for chatbot checkpointing and agent long-term storage.

## Clients

Clients communicate with the chatbot using a websocket at the /chatbot server endpoint, which connects to the LangGraph chatbot. Write a websocket client for a chatbot. Write any JavaScript into /static/js/wikkit.js.

### Input and Ouput

User input and chatbot output are displayed in the #wikkit div, which is contentEditable and scrollable, and which always adds new messages as the last of the div's children, auto-scrolling to bring them into view.

The #wikkit div must be draggable, content editable--with each trimmed non-empty input line being sent to the chatbot--and allow resizing using an affordance in its lower-right corner.

### Processing Server Responses

Regular chatbot responses are HTML and are appended to #wikkit.
JSON responses are commands to the client. For now, the only server-to-client command is "clear" which deletes all of the child nodes of #wikkit.

## Chatbot Nodes or Steps

The chatbot must guide the user through seven steps or nodes to successfully produce a poster.
Each node has its own page which is loaded into the /wikkit iframe. Each node must load the next node.
Some nodes may be visited multiple times, based on conditional edges, before the chat concludes.

Here are the nodes:

### 1. Wikkit Welcome

1. Fetch the user's name from the database.
2. Welcome the user to PrestoPresentations.
3. Tell the user: "I'm going to ask you a few questions to help us create the perfect poster for your needs."
4. Tell the user: "The more information you can provide, the better I can help you."
5. Tell the user: "And you can relax, you can change or update your poster's requirements, preferences, and content at any time until your poster's commercially printable PDF is generated."
6. Tell the user: "With a few minutes and a little magic, we can quickly and easily prepare your conference or fair poster."
7. Tell the user: "Let's begin!"

### 2. Get Conference or Fair Information

1. Ask the user "Please tell me any information you have about the conference or fair where you&rsquote;re presenting, such as its name, subject area, location, or dates:"
2. If the user doesn't have any information about the conference then continue to node #3.
3. If the user's response is empty or all whitespace, then ask the user whether they want to continue without a conference.
   - If the user's next response is negative then repeat Node 2.
4. Use getConferenceInfo in wikkit.py to find any matching conferences for the user's input.
   - If there are no matching conferences:
     - Ask the user whether they have any more conference information to try and, if the user has more information to try, then repeat node 2.
     - Otherwise, go to Node 3.
5. Show the results as an enumerated, formatted, scrollable list with clickable rows in the page.
6. Allow conference selection through clicking on a conference row. Clicking on a row sets the conference information in the long-term store continue to node #3.
7. Ask the user if one of the results is their conference.
8. If the user identifies one of the rows as their conference, then set the conference information in the long-term store continue to node #3.
9. If the user doesn't recognize any conference:
   - Ask the user whether they have any more conference information to try and, if the user has more information to try, then repeat node 2.
   - Otherwise, go to Node 3.

### 3. Content uploading

- Allows drag and drop.
- Allowed content types are PDF's, Word, Excel tables, and PNG, JPEG, JPG, and TIFF images.
- For PDF's
  Use the existing Docling logic to extract the poster content.

1. Extract texts from uploaded PDF's and Word documents.
2. Look for high content correlation about a single subject\.
3. If not then complain to user.
4. Classify images
5. Link images to reference
6. Link math to reference



### 4. Poster Goal

Based upon any conference information and imported content, identifty the poster's goal and supporting content from the following options:

#### Narration: (Relating a story)

Identiry the key events of the source text in order, with each event consisting of:

Return a list of the events:

```
[
	{
	"titles" : {
		"title" : the original or a composed title for the event,
	},
	"events" : [
		{
			"title" : event title,
			"description" : event description,
			"origorder" : original extracction
		}
	},
]
```

#### Elucidation: (Documenting the results of a study)

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
<hr>

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
	"introduction" : an introduction to the study
	"materials and methods" : 
		an overview of the materials and methods employed, 
		use bullet points to highlight the most important methods
	"results : 
		an overview of the results, 
		use bullet points to highlight the most important results
	"conclusions" :
		an overview of the conclusions,
		use bullet points to highlight the most important conclusions
	"literature cited" : 
		a formatted list of any literature cited in thr introduction, materials and methods, and result
	"acknowledgements" : [
		an acknowlegement,
	]
}
```


#### Promotion: (Putting forth one or more ideas or concepts without an obvious thesis)

Identify each item being promoted in order, writing the following for each:

Return a list of the items using the following schema:

```
[
	{
		"title" : 
		"items" : [
			{
				details of the item
			}
		]
	}
]
```

5.  Title

6.  Add a jsonb field to the poster table: purpose, that stores the poster's purpose and inferred elements:

    {
    "type": {narration, elucidation, promotion},
    "elements": [
    {
    "type": {item, thesis, conclusion, claim, item}
    "text": {extracted text}
    },
    ]
    }

7.  Poster layout
    May be null
    If no layout, choose one based on content and "sense of conference"

8.  Poster dimensions
    May be null
    If no dimensions, choose size based on content and "sense of conference"

9.  Storyboarding
    Intents - Content Editing - Add Graph - Add Illustration - Add Table - Environment - Script View → Show Script - Show Script View → Show Script - Show Script → Show Script - Layout View → Show Layout - Show Layout View → Show Layout - Show Layout → Show Layout

    Two views: - Script View ?????? - Layout View

    **Hot poster with dynamic overflow and chatbot warnings | Script** Two Views
    Change Order (text or mouse)
    Hide (or reveal) item
    Delete item
    Rewrite item
    Merge items
    Split item
    _Intent_ Things to emphasize or focus on (each section)

    **CHAT INSTRUCTIONS FOR HOW TO ADJUST RELATIONSHIPS** \* Give a little more attention to section 1 ... over section 2

    > > > > SPARE ELEMENT STORAGE COMMON TO SCRIPT AND LAYOUT
    > > > > CONTENT IS LAID OUT FOLLOWING SEQUENCE NUMBERS

10. Allowed changes (conditional edges)
    Change Layout -> Story Boarding
    Change Dimensions -> Story Boarding
    Change Story Board -> Story Boarding

The user's UX uses the
The user's websocket and chatbot thread must be tracked together: failure of one must lead to an orderly shutdown of the other

The chatbot communicates with the user via the #wikkit element in wikkit.html.

Some LangGraph nodes have specific HTML pages that need to be loaded into the

Source content for the posters will be uploaded by the user

Identify conference
Ask user for identifying information

Add content
Images
Interpret
Graphs
Interpret
Tables
Parse tables
Source documents
Parse

Create content
Graph

# New router: wikkit

Each

Create a general drag and drop mechanism that adds new content and updates storyboard as an asynchronous task.
Inherits from Figma: most recent/last change wins.

New endpoint getConference

    [Wide window over results]

    While True:
    	Get search criteria
    	Search the web
    	If any results
    		Display results
    		If user clicks conference or enters conference match:
    			Set active conference

    	Encourage more information | Continue [without conference information]

    	If not more information: # invert expression: if continue ugh
    		Break

New endpoint nextSteps

New endpoint layoutPoster

New endpoint addContent

New endpoint storyboard

