# Chatbot

We are going to build a chatbot that will help users create print-ready conference posters adding on to the code, logic, and data structures we already have in the repository.

The chatbot utilizes the /wikkit endpoint to load /wikkit/welcome and provide access to /wikkit's overlayed #wikkit div, which is for chatbot interactions with the user while overlaying the active page in the iframe.

The chatbot will be built using LangChain and LangGraph interacting with the main application's webserver, app.py.
Each chat node has its own webpage associated with it, which is loaded into the iframe, underneath the chatbot div, whenever the node is accessed.

Each poster will have one chat associated with it, which must persist alongside the poster.
Deletion of a poster, or of the poster's creater, must delete the chat.

## Intent Classification
Use best choice or bag-of-words for intent classification.

## Checkpoints and Long-term store
Use the existing Postgres database for chatbot checkpointing and agent long-term storage.

## Clients
Clients communicate with the chatbot using a websocket at the /chatbot server endpoint, which connects to the LangGraph chatbot.

### Input and Ouput
User input and chatbot output are displayed in the #wikkit div, which is contentEditable and scrollable, and which always adds new messages as the last of the div's children, auto-scrolling to bring them into view.
The #wikkit div must be draggable, content editable--with each trimmed non-empty input line being sent to the chatbot--and allow resizing using an affordance in its lower-right corner. 

### Processing Server Responses 
Regular chatbot responses are HTML and are appended to #wikkit.
JSON responses are commands to the client. For now, the only server-to-client command is "clear" which empties #wikkit.

## Chatbot Nodes or Steps
The chatbot must guide the user through seven steps or nodes to successfully produce a poster. 
Some nodes may be visited multiple times, based on conditional edges, before a chat concludes.

1. Wikkit Welcome
	Welcome the user to PrestoPresentations.
	Ask the user for their name and add it to the long-term storage.		

2. Get Conference or Fair Information
   Use the logic in wikkit.py
   Ask the user for the name of the conference or fair (such as science, engineering, math). 
   Use getConferenceInfo in wikkit.py to look up conference data.
   Modify getConferenceInfo to accept or use the main server's openAI connection information, rather than requiring a separate connection.

3. Content uploading
   Allows drag and drop.
   Content is PDF's, Excel tables, PNG, JPEG, JPG, and TIFF images.
   Use the existing Docling logic to import the poster content.

4. Poster Purpose
   Based upon any conference information and imported content, infer whether the poster is:
   - Narration (Repeating a story)
   - Elucidation (Thesis, support, conclusion)
   - Promotion (Discussing a variety of subjects)

   If the poster is narration 
   	    - Infer the key events in order.
   If the poster is elucidation:
    	- Infer the thesis, conclusion, and supporting claims in order.
   If the poster is promotion
		- Infer each item being promoted.

	Add a jsonb field to the poster table: purpose, that stores the poster's purpose and inferred elements:
	
	{
		"type": {narration, elucidation, promotion},
		"elements": [
			{
				"type": {event, thesis, conclusion, claim, item}
				"text": {extracted text}
			},
		]
	}

5. Poster layout
	May be null
	If no layout, choose one based on content and "sense of conference"

6. Poster dimensions
	May be null
	If no dimensions, choose size based on content and "sense of conference"

7. Storyboarding
	Intents
		- Content Editing
			- Add Graph
			- Add Illustration
			- Add Table
		- Environment
			- Script View → Show Script
			- Show Script View → Show Script
			- Show Script → Show Script
			- Layout View → Show Layout
			- Show Layout View → Show Layout
			- Show Layout → Show Layout

	Two views:
		- Script View ??????
		- Layout View

	**Hot poster with dynamic overflow and chatbot warnings | Script** Two Views
	Change Order (text or mouse)
	Hide (or reveal) item
	Delete item
	Rewrite item
	Merge items
	Split item
	*Intent* Things to emphasize or focus on (each section)

	**CHAT INSTRUCTIONS FOR HOW TO ADJUST RELATIONSHIPS**
		* Give a little more attention to section 1 ... over section 2

	>>>> SPARE ELEMENT STORAGE COMMON TO SCRIPT AND LAYOUT
	>>>> CONTENT IS LAID OUT FOLLOWING SEQUENCE NUMBERS

8. Allowed changes (conditional edges)
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


