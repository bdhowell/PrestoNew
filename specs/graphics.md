We're creating logic for inserting and managing graphics within different sections of a poster.

A section is active when the user has clicked into the contentEditable portion of the poster.

When #image is clicked:
    If no section is active:
        Wikkit asks the user to select a section into which to insert the graphic.
        Return
    Elif the active section == "Additional Information":
        Wikkit tells the user that only internally generated QR codes can be inserted into the "Additional Information" section.
        If there is already an existing QR code for the poster then
            Wikkit informs the user that a QR code already exists for the poster.
            Wikkit asks the user whether they want to replace the existing QR code.
            If the user answers "No":
                Wikkit says "Okay, we won&rsquo;t replace the existing QR code."
                Return
        Else:
            Wikkit asks the user whether they want to insert an internally generated QR code.
            If the user answers "No":
                Return

        Wikkit prompts the user for the URL of the generated QR code.
        If the user provides a URL:
            The QR code is generated from the provided URL.
            Add the QR code as a source in the database.

        Insert an at the end of the "Additional Information" section, deleting any existing QR code.
    
    If the active section is in ["Literature Cited", "Acknowledgements"]:
        Wikkit informs the user that graphics cannot be inserted into the "Literature Cited" or "Acknowledgements" sections.
        Wikkit adds that those sections are reserved for specific content and should be kept as brief as possible.
        Return
    
    Display a dialog 
        Showing graphics suitable for the active section.
