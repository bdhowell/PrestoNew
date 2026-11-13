IMAGE_TYPES = [ 'svg', 'png', 'jpg', 'jpeg', 'gif', 'tables', 'math' ]
ONLY_MATH = [ 'math' ]

SHOW_ONLY = {
    'abstract': ONLY_MATH,
    'introduction': ONLY_MATH,
    'methods': IMAGE_TYPES,
    'results': IMAGE_TYPES,
    'conclusion': ONLY_MATH
}

On `/content/review`, show the graphics panel only for the categories in ['introduction', 'methods', 'results', 'conclusion'] and keep it hidden for all other categories.

The graphics panel should display only the types of content specified in the `SHOW_ONLY` dictionary for each category.

Methods and Results rank graphics by their type's index in IMAGE_TYPES and then by assessed rating for the section.
Introduction and Conclusion display graphics without ranking.

Add a panel to the right of the graphics panel: "Graphic Assessment".
The graphics assessment panel only displays when `methods` or `results` are the current category.
Clicking on a graphic in the graphics panel outlines the selected graphic and updates the "Graphic Assessment" panel to show the description and section-specific assessment for the selected graphic.
When `methods` or `results` are the current category, display `Graphic Assessment`. 
Select the first graphic in the graphics panel by default and show its description and section-specific assessment in the "Graphic Assessment" panel.
