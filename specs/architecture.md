We are creating a new SaaS FastAPI application to create conference and fair presentation posters and allow their creators to print them and share them with the public.
The application integrates a LangChain and LangGraph chatbot as specified in "Wikkit the Wizard.md"
The application is configured as a Docker container and runs under Docker on the local machine and Google Cloud Run.


The database is PostgreSQL equipped with PGVector. 
Sessions are managed with Redis (installed).
Sessions persist for {SESSION_MAX_AGE}.
Users are notified of incipient session expiration at {SESSION_EXPIRATION_NOTIFICATION=480}.

On-going chats are stored in Postgres to allow review and continuation between sessions.

A preliminary schema is in schema/schema.sql.

Posters are either unpurchased or purchased. 
Unpurchased posters cannot be downloaded.
Purchased posters can be downloaded as native PDF's.

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
Provides the basic chatbot interface..
Provides a viewport-sized iframe with a draggable, resizable, scrollable overlaid div (#chatbot) for the user and the chatbot's interaction and a fixed-height, contentEditable region at the bottom of #chat for user input.
User input messages are moved from the contentEditable region to the bottom of the scrollable region before being sent to the server.
Server responses are appended to the bottom of the scrollable region.
And see Wikkit.md
Accepts a URL as a post parameter to load.

/conferences/find

CONFERENCE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "conference_name",
        "conference_location",
        "conference_start_date",
        "conference_end_date",
        "conference_subject_or_summary",
        "maximum_poster_dimensions",
        "maximum_poster_width",
        "maximum_poster_height",
        "required_poster_dimensions",
        "required_poster_width",
        "required_poster_height",
        "allowed_poster_dimensions",
        "allowed_poster_width",
        "allowed_poster_height",
        "poster_units",
        "poster_layouts",
        "special_poster_attributes",
    ],
    "properties": {
        "conference_name": {
            "type": "string",
            "description": "Name of the conference or fair",
        },
        "conference_location": {
            "type": "object",
            "additionalProperties": False,
            "required": ["country", "state_province", "city"],
            "properties": {
                "country": {
                    "type": "string",
                    "description": "Country of the conference or fair",
                },
                "state_province": {
                    "type": "string",
                    "description": "State or province of the conference or fair",
                },
                "city": {
                    "type": "string",
                    "description": "City of the conference or fair",
                },
            },
        },
        "conference_start_date": {
            "type": "string",
            "description": "Start date of the conference or fair",
        },
        "conference_end_date": {
            "type": "string",
            "description": "End date of the conference or fair",
        },
        "conference_subject_or_summary": {
            "type": "string",
            "description": "Conference subject area or short summary",
        },
        "maximum_poster_dimensions": {
            "type": ["string", "null"],
            "description": "Maximum poster dimensions, if specified",
        },
        "maximum_poster_width": {
            "type": ["integer", "null"],
            "description": "Maximum poster width, if specified",
        },
        "maximum_poster_height": {
            "type": ["integer", "null"],
            "description": "Maximum poster height, if specified",
        },
        "required_poster_dimensions": {
            "type": ["array", "null"],
            "items": {"type": "integer"},
            "description": "Required poster dimensions, if specified",
        },
        "required_poster_width": {
            "type": ["array", "null"],
            "items": {"type": "integer"},
            "description": "Required poster widths, if specified",
        },
        "required_poster_height": {
            "type": ["array", "null"],
            "items": {"type": "integer"},
            "description": "Required poster heights, if specified",
        },        
        "allowed_poster_dimensions": {
            "type": ["array", "null"],
            "items": {"type": "integer"},
            "description": "Allowed poster dimensions, if specified",
        },
        "allowed_poster_width": {
            "type": ["array", "null"],
            "items": {"type": "integer"},
            "description": "Allowed poster widths, if specified",
        },
        "allowed_poster_height": {
            "type": ["array", "null"],
            "items": {"type": "integer"},
            "description": "Allowed poster heights, if specified",
        },
        "poster_units": {
            "type": ["string", "null"],
            "enum": ["inches", "centimeters", "millimeters"],
            "description": "Poster dimensions' units, if specified",
        },
        "poster_layouts": {
            "type": ["array", "null"],
            "items": {
                "type": "string",
                "enum": ["three-column", "four-column", "tri-fold", "results-arena", "flow-layout"]
            },
            "description": "All required or allowed poster layouts, if specified",
        },
        "special_poster_attributes": {
            "type": ["array", "null"],
            "items": {"type": "string"},
            "description": "Any other specified poster requirements or attributes",
        },
    },
}


/content/upload
Uploaded content is stored relative to {SOURCES_DIR} for local development and GCS when
Allows uploading of PDF, Microsoft's DOCX, PPTX and XLSX, and Apple's Pages, Numbers and Keynote
Use docxtractor.py to pull out elements.









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