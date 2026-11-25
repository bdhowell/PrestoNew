When #print is clicked on the editor page ()`/layout/edit`), Wikkit asks the user by name whether they're ready to purchase their poster, after which they will be able to download a printable PDF but they won't be able to make any more changes to their poster.

Wikkit shows buttons <button data-sends="ready">I'm Ready to Print</button> and <button data-sends="cancel">My Poster Isn't Finished</button>

Wait for user input for the buttons.

If "ready" then redirect to `/account/purchase`, for now a placeholder page but eventually where the user will be able to purchase their poster through Stripe. For now, mark the poster as purchased for the price of os.environ["POSTER_PRICE"] and redirect to `/account/download`. Record the poster purchase in the `purchase` table.

`/account/download` renders the poster full-size after which the file is immediately downloaded to the user's browser a printable PDF.