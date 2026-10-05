We are creating a new SaaS FastAPI application to create conference and fair presentation posters and allow their creators to print them and share them with the public.
The application integrates a LangChain and LangGraph chatbot as specified in "Wikkit the Wizard.md"
The application is configured as a Docker container and runs under Docker on the local machine and Google Cloud Run.

The database is PostgreSQL equipped with PGVector. 
Sessions are managed with Redis (installed).
Sessions persist for {SESSION_MAX_AGE}.
Users are notified of incipient session expiration at {SESSION_EXPIRATION_NOTIFICATION=480}.

On-going chats are stored in Postgres to allow review and continuation between sessions.

A preliminary schema is in schema/schema.sql.

Here are the basic endpoints:

/home
Displays a landing page.

/acccount/register
Allows a user to create an account.
Requires a username, password, email address.
Upon completion of registration, send confirmation 

/account/sign-in
sign into an existing account
reset password feature

/account/update
change password
delete acccount

/posters/list
Shows a list of the posters the user has created.
The user is allowed to have only two unprinted posters at once.
The list is sortable by poster name, conference date and supports the semantic search logic described in search.md
The list can be displayed as a vertically scrolling list or an array of large thumbnails.

/wikkit
Provides a viewport-sized iframe with an overlaid 
Accepts a URL as a post parameter to load.

/conferences/find


/content/upload
Uploaded content is stored relative to {SOURCES_DIR} for local development and GCS when
Allows uploading of PDF, Microsoft's DOCX, PPTX and XLSX, and Apple's Pages, Numbers and Keynote
Use docxtractor.py 








Data Extraction
    Use Docling
    Parse 
    Extract source text with image references
    
    Get texts: must all refer to one study
    Analyze the supplied texts?, which (is|are) about (a) scientific stud(y|ies).




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