# Client UX

## Embedded HTML controls in chats

After any wait for user input is satisfied, any HTML controls in the chat flow become inactive.

## Clicking an Element with a Visible Outline

Clicking an element with a visible outline hides its outline, if any.

# Fonts and Layout

Add a --header-background-color variable to each theme which sets the background-color of #poster-header.
Add a --header-font-color variable to each theme which sets the font color of #poster-header.

Create six new themes with #poster-header having --header-background-color and --header-font-color values coordinated with #poster-content's --background-color and #text-color

## Base Font Size

The base font-size is computed as:

`2rem * (poster area in square cm / minimum poster area in square cm)`

Use the physical poster area, rather than the live rendered `#layout` area, so
wrapping, header height, zoom, and auto-fit do not feed back into the base size.
The minimum poster area is 91.44 cm x 91.44 cm (36 in x 36 in).

Line spacing is also scaled proportionally.

`#authors` and `#title` base font sizes and line spacing are proportional to the `#layout` font.

## Font Sizing Controls

Inside `#extended-controls`, on the container's right side, add a drop-down menu `#font-sizing`: Sections, Title, Authors
Add a font-size slider on the right of `#font-sizing` that adjusts the `#layout` font size and line spacing each section up to 1.5x from the default/initial value.
The drop-down controls which of the poster's fonts the slider is modifying.
Indicate these controls' purpose using Font Awesome's <i class="fa-solid fa-text-size"></i> positioned before the drop-down menu.
The slider ranges from 50% to 150%, with 100% restoring the area-based default.
Each target has an independent saved multiplier; switching targets restores its
slider value. Changes preview immediately and scale line spacing proportionally.
Save these settings with the poster, preserve them across template/font/theme
changes, and apply them to read-only previews. Report save errors with a retry
action and warn before navigating away with unsaved adjustments.

= = = = = = = = = = = = = = = = = = = = = = = = = = = = = = = = = = = = = = = = = = = = = = = = =

When a poster is created, fix its resizeable areas to the initial layout dimensions, so that subsequent adjustments to font sizing or content do not alter the original layout proportions.

# Graphics Handling

Within the context of this application, *graphics* may be PNGs or JPGs; SVG; Latex or ASCII Math rendered with MathJaxo; or charts rendered with Chart.js.

Graphics are inserted into section text and may be cut, copied, or pasted.

# Graphic Placement

Graphics can snap to eight locations within the section:

1. top-left, 
2. top-center, 
3. top-right,
4. left-middle,
5. right-middle,
6. bottom-left, 
7. bottom-center,
8. bottom-right

If a graphic is not snapped to one of those eight points, then it is free-floating within the text.
If a graphic is between two paragraphs and not snapped, then the graphic is implicitly centered vertically.

Clicking a graphic toggles resize handles at the corners and centered along each edge.

## Dimensions

After resizing a graphic, if the pixel density drops below GRAPHICS_SAFE_DPI:
- Flash the graphic to indicate it may be too low resolution.
- Wikkit says: 
f"""
{identity.user_name}, your graphic is likely to be blurry to most viewers.
"""

## On Vertical Overflow

If inserting or resizing a graphic causes vertical overflow 
- Do magic.auto_fit() on the section's layout and content overflow.

If resizing the `#layout` font causes vertical overflow -->
- Do magic.auto_fit() on the section's layout and content overflow.

============================================================

# Content.py Additions

## Image Serving

Add a route `/image` that takes an image source ID (SID), verifies that the user owns it, and returns the image if the verification succeeds.
Otherwise, it returns an HTTP 404 Not Found response.

## Evaluating Graphics Suitability
Add a route `/evaluate` that takes a poster ID and evaluates a poster's graphic sources using evaluation.py.
Invoke it after a successful elucidation

### Template Content
Wikkit is evaluating the suitability of each graphic you&rsquo;ve uploaded for your poster.

### Internal Logic
This is a database request
Select graphics being evaluated in the poster that have a null parent, as source.sid, "uploaded"
Union all
Select graphics extracted from the source text(s), that is having a non-null parent, as source.sid, "extracted"

If the number of graphics being evaluated is zero:
- Wikkit says: 
f"""
Well, that was quick! 
I didn&rsquo;t find any graphics to evaluate. 
We&rsquo;re going to need some but we&rsquo;ll get around to graphics shortly.
""" 
- redirect to `/conferences/query`

uploaded_graphics_count = len([g for g in graphics if g['type'] == 'uploaded'])
extracted_graphics_count = len([g for g in graphics if g['type'] == 'extracted'])
graphics_evaluating = uploaded_graphics_count + extracted_graphics_count

message = "I&rsquo; reviewing the "
if uploaded_graphics_count:
	message += f"{num2words(uploaded_graphics_count)} graphic{common.pluralize(uploaded_graphics_count)} you uploaded"
	if extracted_graphics_count:
		message += " and "
if extracted_graphics_count:
	message += f"{num2words(extracted_graphics_count)} graphic{common.pluralize(extracted_graphics_count)} I was able to extract from the source text(s)"

Wikkit says f"""
Okay, {identity.user_name}, {message}.
Give me a few more moments to evaluate them.
"""

processed = 1
for each graphic in graphics:
	# Evaluate the graphic's suitability
	suitable = evaluation.evaluate(graphic)
	# Store the evaluation result in the graphic dictionary
	graphic['suitable'] = suitable
	Wikkit says f"""
	Processing image {num2words(processed)} of {num2words(graphics_evaluating)}.
"""
	processed += 1

After evaluating all graphics, display a summary of which graphics are suitable and which are not.
Order the graphics by uploaded and then extracted, with a clear distinction between the two categories.

============================================================

## Wand and Wikkit Interactions

When Wikkit appears on the poster page, he implicitly checks the whole poster and reports on delta changes. 
Toggling Wikkit off turns off any outlines in the poster.
Concerning graphics and sections are outlined.

Tapping the wand on the poster, exclusive of any sections, (re-)assesses the entire poster.
Tapping the wand on a section displays an assessment of the section, and any remedies and recommendations.
	Try resizing an overflowing graphic
	Implicit metric: Any text embedded in the graphic should be approximately the same size as poster text.
Tapping the wand on a graphic displays an assessment, and any remedies and recommendations.
	If overflowing:
		If reasonable to resize the graphic 
			<button>Resize Automatically</button>
		Else
			Indicate that the graphic cannot be resized automatically.
	If underflowing:
		If reasonable to resize the graphic 
			<button>Resize Automatically</button>
		Else
			Indicate that the graphic cannot be resized automatically.

Create a table named `sizings` in 
`	pid		INTEGER NOT NULL REFERENCES poster(pid) ON DELETE CASCADE,
	section		section_which NOT NULL,
	length		SMALLINT NOT NULL,
	version		TIMESTAMPZ NOT NULL'

============================================================

# Create a Python module and router named `magic`

SECTIONS = [    
	"title", "abstract", "introduction", "methods", "results", "conclusion", 
	"plans", "citations", "information", "acknowledgements",
]

TOO_LONG = '>'
TOO_SHORT = '<'
JUST_RIGHT = '='

GRAPHICS_SAFE_DPI=150

GRAPHICS_EXTREMELY_TALL = os.environ.get('EXTREMELY_TALL', 0.50)
GRAPHICS_TOO_TALL = os.environ.get('TOO_TALL', 0.33)
GRAPHICS_TOO_WIDE = os.environ.get('TOO_WIDE', 0.33)

## Create a function, `def friendly_list(items:list[str]):
	def friendly_list(items: list[str]) -> str:
		if not items:
			return ""
		if len(items) == 1:
			return items[0]
		return ", ".join(items[:-1]) + " and " + items[-1]

## Create a function, `def profile_poster(pid:int):`
```python
section_min_area = os.environ.get('SECTION_MIN_OCCUPIED', 30) / 100
section_max_area = os.environ.get('SECTION_MAX_OCCUPIED', 75) / 100
poster_underflow = os.environ.get('SECTION_UNDERFLOW_OCCUPIED', 0.40) / 100

overflowing_sections = []
underflowing_sections = []
all_oversized_graphics = []

total_area = 0
occupied_area = 0

sections = {}

Using the playwright library:
dom_sections = await page.locator('.section').all()

for section_element in dom_sections:
	details = {}
	name = await section_element.evaluate("element => element.ID")
	sections[name] = details
	oversized_graphics = []
	graphics = []

	for graphic in await section_element.locator('.graphic').all(): # graphic DOM elements within the section
		width_ratio = graphic.width / await section_element.evaluate("element => element.offsetWidth")
		height_ratio = graphic.height / await section_element.evaluate("element => element.offsetHeight")

		too_wide = width_ratio > GRAPHICS_TOO_WIDE
		too_tall = height_ratio > (GRAPHICS_TOO_TALL if name != 'results' else GRAPHICS_EXTREMELY_TALL)

		if too_wide or too_tall:
			oversized_graphics.append((name, section_element, graphic.id, width_ratio, height_ratio, too_wide, too_tall))

		occupied_area = graphic.width * graphic.height
		occupied_percentage = occupied_area / details['area']

		graphic = {
			"id": graphic.id,
			"width": graphic.width,
			"height": graphic.height,
			"area": occupied_area,
			"percentage": occupied_percentage,	
			"overflow": occupied_percentage > 1.00,
			"underflow": occupied_percentage < poster_underflow,
			"too_wide": too_wide,
			"too_tall": too_tall,
			"oversized": too_wide or too_tall,
		}
		graphics.append(graphic)

	all_oversized_graphics.extend(oversized_graphics)

	details['area'] = await section_element.evaluate("element => element.offsetWidth * element.offsetHeight")
	details['occupied'] = occupied_area = sum(graphic['width'] * graphic['height'] for graphic in graphics)
	details['density'] = details['occupied'] / details['area']

	if details['density'] < section_min_area:
		underflowing_sections.append(name)
	if details['density'] > section_max_area:
		overflowing_sections.append(name)

	total_area += details['area']
	occupied_area += details['occupied']

	poster_density = occupied_area / total_area

	details['overflow'] = details['density'] > 1.00
	details['underflow'] = details['density'] < section_min_area
	
	details['fit'] = TOO_SHORT if details['density'] < section_min_area \
				 else TOO_LONG if details['density'] > section_max_area \
				 else JUST_RIGHT

	details['oversized_graphics'] = oversized_graphics
	details['graphics'] = graphics

return {
    "total_area": total_area,
    "occupied_area": occupied_area,
	"density": poster_density,
	"sections": sections,
	"overflowing_sections": overflowing_sections,
	"underflowing_sections": underflowing_sections,	
	"oversized_graphics": all_oversized_graphics,
}
```

# Asssessing:
# 
#   Images that are too large or too small within their sections will be highlighted for resizing.
#   Sections that are underfilled or overfilled will be flagged for adjustment.
#    
#	Returns 

## Create a Wikkit flow, `assess_poster(chatbot bot, pid:int, sections:[str]|None=None, images:bool=True):`
```python
profile = profile_poster(pid)

oversized_plural = 's' if len(profile.oversized_graphics) > 1 else ''
scope = "those sections" if len(section_names) > 1 else "that section"

if len(profile.oversized_graphics):
Wikkit says: 
f"""
<p>
So, {identity.username}, your graphics in {friendly_list(section_names)} are taking up quite a bit of space. 

##
assesss_poster(bot, pid, None)
Do you want to try resizing the graphic{oversized_plural} in {scope} and then you can still use auto-fit after that?
</p>

<p>
<button data-sends="resize">I&rsquo;ll Resize the Graphics</button> &nbsp; <button data-sends="auto-fit">Auto-fit {scope}&rsquo; Now</button>
</p>
"""

- Flash an outline three times on the graphics(s) that could be resized. 
	- Use a server-client command: {command: 'flash', 'payload': [ '#graphic_id', ]} // the payload is a list of css section selectors whose outlines are to be flashed and then left visible. 
	
Wait for user input
If 'resize':
- Wikkit says: f"Ok, {identity.username}, make your adjustments and then let me know that you&rsquo;re ready to auto-fit your text and I&rquo;ll get right on it.



## Create a function, `def auto_fit(pid:int, sections:[str]|None=None):
Determine amount of vertical overflow
word_count = count words in existing text
word_target = word_count * percentage of the text's vertical visual overflow

check the `sizings` table for a sizing with a word count +/- os.env.get('SIZINGS_WORDS_PERCENTAGE', 8%) of the percentage of vertical overflow

source_text = the full-length original section text
prior_text = source_text

while True:
- summary_text = Use gpt-4o-mini to summarize source_text in {scaled word count}
- Render text
- If content is overflowing:
	- Cut word count in half
	- prior_text = summary_text
- Elif content is underflowing by more than 15%:
	- Increase word count by half
	- prior_text = summary_text
- Else:
	- break

If less than 20% of the original word count:
- Restore prior_text
- Wikkit says "I &rsquo;m sorry {identity.username}, can&rsquo;t make your text any shorter without it losing too much clarity.
	- Wikkit says "You might consider resizing the large graphic within the text".
	- Flash the outlines of the graphics that could be resized using the server-client command: {command: 'flash', 'payload': [ '#graphic_id', ]}
- break
