Yes. You can estimate **how clearly the individual features of a diagram will be visible at a specified viewing distance**, given the diagram's physical dimensions and its image content.

For scientific presentation posters, I would recommend a **perceptual legibility metric based on visual angle, feature size, contrast, and spatial frequency**.

The important distinction is between a diagram being *detectable* (someone can see that it is there), *discernible* (someone can distinguish its important features), and *interpretable* (someone can understand the information it conveys).

These are three different levels of visual performance.

## **1. Visual angle: the fundamental measurement**

The apparent size of a feature depends on its physical dimensions and viewing distance.

For a feature of physical width \\(s\\), viewed from distance \\(d\\), its visual angle is:

$$
\theta=2\arctan\left(\frac{s}{2d}\right)
$$

For small angles, expressed in arcminutes:

$$
\theta_{\text{arcmin}}\approx3438\frac{s}{d}
$$

Here, $s$ and $d$ must use the same units.

For a viewing distance of 3 feet (914.4 mm):

| Feature size | Visual angle |
| ------------ | ------------ |
| 0.1 mm       | 0.38 arcmin  |
| 0.25 mm      | 0.94 arcmin  |
| 0.5 mm       | 1.88 arcmin  |
| 1 mm         | 3.76 arcmin  |
| 2 mm         | 7.52 arcmin  |
| 5 mm         | 18.8 arcmin  |

For comparison, conventional 20/20 visual acuity corresponds to resolving approximately 1 arcminute of angular separation under favorable, high-contrast conditions.

However, this represents a threshold for simple features under controlled conditions, not a practical target for complex scientific diagrams.

A feature subtending 1 arcminute may technically be resolvable but still be difficult to see reliably.

## **2. Feature visibility involves more than size**

Four attributes are especially important:

| Attribute            | What it measures                                            | Why it matters                                                     |
| -------------------- | ----------------------------------------------------------- | ------------------------------------------------------------------ |
| Angular feature size | Apparent size of lines, symbols, and details                | Small details become unresolvable                                  |
| Local contrast       | Difference between a feature and its surrounding background | Low-contrast features need to be larger                            |
| Spatial frequency    | Density of repeating details, edges, and patterns           | Fine structures merge together                                     |
| Visual crowding      | Proximity of neighboring features                           | Separately resolvable features can become difficult to distinguish |

For example, a thin black line against white may be visible at a smaller angular size than a light-gray line against white. Two thin parallel lines also require sufficient separation to remain distinguishable.

A useful evaluator therefore needs to analyze the image itself, not simply its dimensions.

## **3. A practical algorithm for diagram evaluation**

Suppose you have:

- A raster image of width \\(W\\) and height \\(H\\), in pixels.
- A proposed display width \\(w\\) and height \\(h\\), in millimeters.
- A viewing distance \\(d\\), in millimeters.

You can calculate how much visual detail survives when the image is viewed at the proposed size.

### **Stage A: Convert pixels into visual angles**

For an image displayed without distortion:

$$
\text{mm per pixel}=\frac{w}{W}=\frac{h}{H}
$$

Therefore:

\\[ \text{arcmin per pixel} \approx3438\frac{w}{Wd} \\]

This conversion lets you express every detected image feature in angular units.

### **Stage B: Analyze the diagram's features**

Use computer vision to identify:

1. Line widths, including the thinnest important lines.
2. Spacing between adjacent lines and structures.
3. Text heights and stroke widths.
4. Small symbols, markers, and graphical elements.
5. Local luminance contrast.
6. Dense or highly detailed regions.

OpenCV can provide much of this information using edge detection, distance transforms, connected-component analysis, and local contrast measurements.

Unlike a global edge-density metric, these measurements capture the actual size and separation of features.

### **Stage C: Apply a perceptual visibility model**

A useful approach is to use a **contrast sensitivity function (CSF)**, which models the human visual system's sensitivity to different spatial frequencies.

Contrast sensitivity is not constant: the eye is generally more sensitive to intermediate spatial frequencies than to extremely fine detail.&#x20;

[image](https://www.google.com/s2/favicons?domain=https://www.ncbi.nlm.nih.gov\&sz=32)

NCBI+1



A simplified model would be:

$$
V_i=\frac{C_i}{C_{\text{threshold}}(f_i)}
$$

Where:

- \\(V_i\\) is the visibility margin of feature \\(i\\).
- \\(C_i\\) is its measured contrast.
- \\(f_i\\) is its angular spatial frequency.
- \\(C\_{\text{threshold}}\\) is the contrast needed to detect that frequency.

A visibility margin greater than 1 suggests that a feature exceeds the modeled detection threshold.

This is still a detection model, not a guarantee of recognition. A detailed model should also account for crowding, feature importance, viewing conditions, and the age or acuity of the intended audience.

## **4. Calculate the minimum acceptable diagram size**

For poster-layout optimizer, I would reverse the problem.

Instead of evaluating a diagram only at its current dimensions, determine its **minimum acceptable display dimensions at 3 feet**.

Let:

$$
V(w,h,d)
$$

represent a perceptual legibility score obtained by analyzing the diagram at physical size \\(w \times h\\) and distance \\(d\\).

You can then search for the smallest scale factor \\(k\\) such that:

$$
V(kw_0,kh_0,d)\ge V\_{\min}
$$

This is a one-dimensional search because the diagram's aspect ratio remains fixed.

A binary search is usually sufficient, provided the chosen visibility score is approximately monotonic with increasing size.

### **Example: minimum feature size at 3 feet**

An ISO ergonomics technical report references a minimum character or symbol visual angle of 20 arcminutes for legibility. That corresponds to roughly 5.3 mm at 914.4 mm viewing distance. It is a useful reference for labels and symbols, but not a universal requirement for every graphical feature.&#x20;

[image](https://www.google.com/s2/favicons?domain=https://preview.sist.si\&sz=32)

ISO/TR 9241-311:2022



Interactive feature-size calculator

Viewing distance

3.0 ft

Target feature visual angle

20 arcmin

Required physical feature size

# **5.32 mm**

0.209 inches

This calculator models angular size only, not contrast, crowding, or complete diagram legibility.

## **5. Recommended implementation for layout system**

I would design a Python evaluator with this interface:

```
evaluate_legibility(    image,    display_width_mm,    display_height_mm,    viewing_distance_mm=914.4,)
```

It would return something like:

```json
{
        "visibility_score": 0.87,    
        "minimum_width_mm": 220,    
        "minimum_height_mm": 145,    
        "fraction_features_visible": 0.94,    
        "critical_features_visible": False,    
        "limiting_factor": "small_labels",    
        "confidence": 0.81
}
```

These values are illustrative, not the result of an actual evaluation. Meaningful scores would require a defined perceptual model and preferably empirical calibration.

For scientific poster applications, I would distinguish between two complementary measures:

**Global perceptual complexity:** How much visual information the diagram contains, using the existing area-adjusted edge-detection metric.

**Feature legibility:** Whether the diagram's individual elements remain distinguishable at the chosen physical size and viewing distance.

A complex diagram may remain perfectly legible if sufficiently enlarged. Conversely, a simple diagram may be illegible because it contains one tiny but essential label.

**My recommendation is to make minimum acceptable diagram size a hard constraint in the layout optimizer**, rather than simply incorporating legibility into a weighted objective function.

This would allow the optimizer to reduce the diagram's size only until the first important visual feature approaches its minimum acceptable legibility. Beyond that point, the layout would need to change the text content or report that the container cannot accommodate the diagram.

The most important implementation decision is whether to use a purely mathematical vision model or supplement it with an image-understanding model that identifies which features are scientifically essential. Combining the two would give you a substantially more useful result than edge density alone.

Yes. In fact, I would recommend treating **minimum acceptable diagram size as a parameterized function**, rather than as a fixed width or height.

The function would determine the smallest physical dimensions at which a diagram meets a specified level of visual legibility.

## **1. Parameterized model**

Define:
$$
[ S_{\min}=f(I,d,L,A,C) ]
$$

Where:

- $I$: The diagram's image content, including its features, text, and spatial frequencies.
- $d$: Viewing distance.
- $L$: Required legibility level.
- $A$: Assumed viewer visual acuity.
- $C$: Viewing conditions, including contrast sensitivity and illumination.

The result $$S_{\min}$$ is the minimum acceptable scale factor, from which you obtain the diagram's physical width and height.

Since the aspect ratio is fixed, only one dimension needs to be optimized.

## **2. Adjustable legibility thresholds**

Define:
$$
L = f(S{min}, I, d, A, C)
$$

I would expose a few meaningful parameters rather than requiring the caller to understand the underlying visual perception calculations.

| Parameter                    | Purpose                                                    | Example         |
| ---------------------------- | ---------------------------------------------------------- | --------------- |
| `viewing_distance_mm`        | Distance from the viewer                                   | 914.4           |
| `legibility_level`           | Required degree of readability                             | `"comfortable"` |
| `visual_acuity`              | Assumed viewer acuity                                      | 1.0 (20/20)     |
| `min_feature_visibility`     | Minimum acceptable feature visibility margin               | 1.5             |
| `required_feature_fraction`  | Fraction of relevant features that must meet the threshold | 0.95            |
| `critical_features_required` | Whether all designated essential features must be legible  | `True`          |

The visibility threshold should be interpreted as a model-dependent margin above the estimated detection threshold, not a directly measured probability of recognition.

You could define legibility presets:

| Level         | Interpretation                                                                        |
| ------------- | ------------------------------------------------------------------------------------- |
| `minimum`     | Essential features are just distinguishable                                           |
| `standard`    | Most features are reliably distinguishable                                            |
| `comfortable` | Features can be distinguished without substantial effort                              |
| `high`        | Greater tolerance for visual acuity differences and less favorable viewing conditions |

These presets would map to calibrated thresholds, rather than arbitrary fixed physical sizes.

## **3. Suggested Python interface**

```
result = minimum_diagram_size(    image=diagram,    viewing_distance_mm=914.4,    legibility_level="comfortable",    visual_acuity=1.0,    required_feature_fraction=0.95,    critical_features_required=True,    max_width_mm=400,    max_height_mm=300,)
```

A result might contain:

```json
{    
    "minimum_width_mm": 238.0,    
    "minimum_height_mm": 159.0,    
    "scale_factor": 1.32,    
    "legibility_score": 0.95,    
    "limiting_features": [        
        "small_text",        
        "closely_spaced_lines"    
    ],    
    "fits_constraints": True
}
```

Again, the numerical results are illustrative.

## **4. A more useful parameter: minimum angular detail**

For a scientific poster, I would also provide a parameter that directly controls the required apparent size of important features:

```
minimum_feature_angle_arcmin = 5.0
```

This could serve as a baseline for fine graphical details, while text and symbols use their own thresholds.

For a detected feature of width $$p_i$$ pixels in an image of width $$W$$, its physical width at displayed diagram width $$w$$ is:

$$
s_i=\frac{p_i}{W}w
$$

Therefore, the minimum diagram width satisfying a specified angular feature threshold $$\theta\_{\min}$$ is:

$$
\boxed{ w\_{\min}= \frac{W}{p_i} \\,2d\tan\left(\frac{\theta\_{\min}}{2}\right) }
$$

Here, $$\theta\_{\min}$$ is expressed in radians.

This equation provides a direct relationship between image content, viewing distance, and minimum diagram size.

For multiple important features:

$$
\boxed{ w\_{\min}=\max_i \left[ \frac{W}{p_i} \\,2d\tan\left(\frac{\theta\_{\min,i}}{2}\right) \right] }
$$

Each feature can have its own threshold, depending on whether it is a line, symbol, text label, or other graphical element.

This assumes the relevant features can be adequately characterized by angular size; contrast and crowding may impose additional constraints.

## **5. Integration with the poster layout optimizer**

I would separate the problem into two functions:

```
minimum_diagram_size(image, viewing_parameters)
```

Calculates the smallest acceptable physical dimensions, based on the diagram's contents and the specified viewing conditions.

```
optimize_layout(container, diagram, minimum_size)
```

Fits the diagram and text into the available container without shrinking the diagram below that minimum.

**An important optimization:** The computationally expensive feature analysis can be performed once per image. The resulting feature measurements can be cached, and the minimum acceptable dimensions recalculated whenever the viewing parameters or legibility requirements change.

That is especially useful when the user repeatedly resizes a poster container.

One further refinement I would strongly recommend is allowing **different legibility requirements for different feature classes**. Text, thin lines, plotted data points, axes, and decorative details should not all have to meet the same angular-size threshold.

This gives you a parameterized model that is both computationally efficient and sensitive to the actual scientific content of the diagram.

# Python module for diagram legibility

There is a Python module, `legibility.py`, that estimates the **minimum acceptable physical dimensions of a scientific diagram** at a configurable viewing distance.

**legibility.py**

Python module · 21 KB

&#x20;Tested

Download Python module

Download unit tests

## **Capabilities**

The module provides:

- **Automatic feature analysis:** Uses OpenCV to identify lines, symbols, and small graphical components.
- **Parameterized legibility:** Configurable viewing distance, visual acuity, contrast sensitivity heuristic, and four legibility presets.
- **Feature-specific thresholds:** Separate angular-size requirements for text, lines, symbols, and fine details.
- **Critical features:** Allows essential elements to be designated as mandatory.
- **Minimum-size calculation:** Returns the smallest acceptable width and height while preserving aspect ratio.
- **Cached analysis:** Extracts image features once, then reuses the measurements for subsequent layout calculations.
- **Layout constraints:** Reports when the minimum acceptable dimensions exceed the available space.

## **Example**

```
from legibility import (    DiagramAnalyzer,    ViewingParameters)analyzer = DiagramAnalyzer.from_file("diagram.png")params = ViewingParameters(    distance_mm=914.4,    legibility_level="comfortable",    visual_acuity=1.0,    required_feature_fraction=0.95,    critical_features_required=True)result = analyzer.minimum_size(    params,    max_width_mm=400,    max_height_mm=300)print(result.to_dict())
```

The same analyzer can evaluate a diagram at any proposed width:

```
evaluation = analyzer.evaluate(    width_mm=250,    params=params)print(evaluation.passes)print(evaluation.visible_fraction)
```

## **Integration with poster-layout optimizer**

The optimizer can treat `minimum_width_mm` and `minimum_height_mm` as hard constraints when resizing a diagram.

The module deliberately separates feature extraction from physical-size calculations, making repeated evaluations inexpensive.

**Important limitation:** The implementation uses configurable angular-size and contrast heuristics, not a clinically validated human visual-perception model. Automatic text recognition and scientific feature importance are not inferred; important text regions and critical elements can be explicitly annotated. This makes the module suitable for an initial poster-layout system, with empirical calibration advisable before relying on its thresholds.

**Testing:** Included unit tests cover cached evaluation, viewing-distance scaling,
manual annotations, image bounds, aspect ratios, and empty diagrams.

## Persisted image score

The source-processing pipeline stores `source.legibility` as the fraction of
automatically detected image features that meet the configured visibility
threshold. Raster and vector assets are scored at their intrinsic display size
using `DIAGRAM_LEGIBILITY_DISPLAY_DPI`; vectors are measured from their paired
raster rendering while their SVG viewBox determines physical size. Scores are
screening heuristics, not probabilities of recognition or clinically validated
measures.

Configure scoring in `ENV` or `.env`:

| Variable | Default | Purpose |
| --- | ---: | --- |
| `DIAGRAM_LEGIBILITY_VIEWING_DISTANCE` | `36.0` | Viewing distance in the selected unit |
| `DIAGRAM_LEGIBILITY_VIEWING_DISTANCE_UNIT` | `inches` | `inches` or `centimeters` |
| `DIAGRAM_LEGIBILITY_TARGET_FEATURE_ANGLE_ARCMIN` | `5.0` | Minimum target angle for feature strokes and extents |
| `DIAGRAM_LEGIBILITY_LEVEL` | `comfortable` | `minimum`, `standard`, `comfortable`, or `high` |
| `DIAGRAM_LEGIBILITY_VISUAL_ACUITY` | `1.0` | Acuity relative to 20/20 |
| `DIAGRAM_LEGIBILITY_REQUIRED_FEATURE_FRACTION` | `0.95` | Fraction of detected features required to pass |
| `DIAGRAM_LEGIBILITY_CRITICAL_FEATURES_REQUIRED` | `true` | Require all explicitly critical features to pass |
| `DIAGRAM_LEGIBILITY_CONTRAST_REFERENCE` | `0.45` | Reference contrast for the heuristic penalty |
| `DIAGRAM_LEGIBILITY_CONTRAST_FLOOR` | `0.04` | Minimum contrast used by the penalty |
| `DIAGRAM_LEGIBILITY_MAX_CONTRAST_PENALTY` | `4.0` | Cap on the contrast penalty |
| `DIAGRAM_LEGIBILITY_DISPLAY_DPI` | `96.0` | CSS pixels per printed inch used to estimate physical size |

The target angle is applied uniformly to detected feature classes; the chosen
legibility level further scales its required margin. Distance units and boolean
values are validated, as are positive numeric inputs and the feature fraction.
