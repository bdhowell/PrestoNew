# Image Complexity

You can estimate how clearly the individual features of a diagram will be visible at a specified viewing distance, given the diagram's physical dimensions and its image content.

For scientific presentation posters, I would recommend a perceptual legibility metric based on visual angle, feature size, contrast, and spatial frequency.

The important distinction is between a diagram being detectable (someone can see that it is there), discernible (someone can distinguish its important features), and interpretable (someone can understand the information it conveys).

These are three different levels of visual performance:

You can estimate how clearly the individual features of a diagram will be visible at a specified viewing distance, given the diagram's physical dimensions and its image content.

For scientific presentation posters, I would recommend a perceptual legibility metric based on visual angle, feature size, contrast, and spatial frequency.

The important distinction is between a diagram being detectable (someone can see that it is there), discernible (someone can distinguish its important features), and interpretable (someone can understand the information it conveys).

## Three levels of visual performance
These are three different levels of visual performance.

### 1. Visual angle: the fundamental measurement
The apparent size of a feature depends on its physical dimensions and viewing distance.

For a feature of physical width $s$, viewed from distance $d$, its visual angle is:

$$[
\theta=2\arctan\left(\frac{s}{2d}\right)
]$$

For small angles, expressed in arcminutes:
$$[
\theta_{\text{arcmin}}\approx3438\frac{s}{d}
]$$

Here, $s$ and $d$ must use the same units.

For a viewing distance of 3 feet (914.4 mm):
Feature size | Visual angle
---|---
0.1 mm	| 0.38 arcmin
0.25 mm	| 0.94 arcmin
0.5 mm	| 1.88 arcmin
1 mm	| 3.76 arcmin
2 mm	| 7.52 arcmin
5 mm	| 18.8 arcmin

For comparison, conventional 20/20 visual acuity corresponds to resolving approximately 1 arcminute of angular separation under favorable, high-contrast conditions.

However, this represents a threshold for simple features under controlled conditions, not a practical target for complex scientific diagrams.

A feature subtending 1 arcminute may technically be resolvable but still be difficult to see reliably.
### 2. Feature visibility involves more than size
Four attributes are especially important:
Attribute	What it measures	Why it matters
Angular feature size	Apparent size of lines, symbols, and details	Small details become unresolvable
Local contrast	Difference between a feature and its surrounding background	Low-contrast features need to be larger
Spatial frequency	Density of repeating details, edges, and patterns	Fine structures merge together
Visual crowding	Proximity of neighboring features	Separately resolvable features can become difficult to distinguish

For example, a thin black line against white may be visible at a smaller angular size than a light-gray line against white. Two thin parallel lines also require sufficient separation to remain distinguishable.

A useful evaluator therefore needs to analyze the image itself, not simply its dimensions.
### 3. A practical algorithm for diagram evaluation
Suppose you have:
- A raster image of width \(W\) and height \(H\), in pixels.
- A proposed display width \(w\) and height \(h\), in millimeters.
- A viewing distance \(d\), in millimeters.
You can calculate how much visual detail survives when the image is viewed at the proposed size.
Stage A: Convert pixels into visual angles
For an image displayed without distortion:
$$[
\text{mm per pixel}=\frac{w}{W}=\frac{h}{H}
]$$

Therefore:
$$[
\text{arcmin per pixel}
\approx3438\frac{w}{Wd}
]$$

This conversion lets you express every detected image feature in angular units.
Stage B: Analyze the diagram's features
Use computer vision to identify:
1. Line widths, including the thinnest important lines.
2. Spacing between adjacent lines and structures.
3. Text heights and stroke widths.
4. Small symbols, markers, and graphical elements.
5. Local luminance contrast.
6. Dense or highly detailed regions.
OpenCV can provide much of this information using edge detection, distance transforms, connected-component analysis, and local contrast measurements.

Unlike a global edge-density metric, these measurements capture the actual size and separation of features.
Stage C: Apply a perceptual visibility model
A useful approach is to use a contrast sensitivity function (CSF), which models the human visual system's sensitivity to different spatial frequencies.

Contrast sensitivity is not constant: the eye is generally more sensitive to intermediate spatial frequencies than to extremely fine detail. 
NCBI+1


A simplified model would be:
$$[
V_i=\frac{C_i}{C_{\text{threshold}}(f_i)}
]$$

Where:
- $V_i$ is the visibility margin of feature $i$.
- $C_i$ is its measured contrast.
- $f_i$ is its angular spatial frequency.
- $C_{\text{threshold}}$ is the contrast needed to detect that frequency.
A visibility margin greater than 1 suggests that a feature exceeds the modeled detection threshold.

This is still a detection model, not a guarantee of recognition. A detailed model should also account for crowding, feature importance, viewing conditions, and the age or acuity of the intended audience.
4. Calculate the minimum acceptable diagram size
For your poster-layout optimizer, I would reverse the problem.

Instead of evaluating a diagram only at its current dimensions, determine its minimum acceptable display dimensions at 3 feet.

Let:
$$[
V(w,h,d)
]$$

represent a perceptual legibility score obtained by analyzing the diagram at physical size $w \times h$ and distance $d$.

You can then search for the smallest scale factor \(k\) such that:
$$[
V(kw_0,kh_0,d)\ge V_{\min}
]$$

This is a one-dimensional search because the diagram's aspect ratio remains fixed.

A binary search is usually sufficient, provided the chosen visibility score is approximately monotonic with increasing size.
Example: minimum feature size at 3 feet
An ISO ergonomics technical report references a minimum character or symbol visual angle of 20 arcminutes for legibility. That corresponds to roughly 5.3 mm at 914.4 mm viewing distance. It is a useful reference for labels and symbols, but not a universal requirement for every graphical feature.

### 1. Visual angle: the fundamental measurement
The apparent size of a feature depends on its physical dimensions and viewing distance.

For a feature of physical width $s$, viewed from distance $d$, its visual angle is:
$$
|\theta=2\arctan\left(\frac{s}{2d}\right)
$$

For small angles, expressed in arcminutes:
$$
\theta_{\text{arcmin}}\approx3438\frac{s}{d}
$$

Here, \(s\) and \(d\) must use the same units.

For a viewing distance of 3 feet (914.4 mm):

Feature size | Visual angle
-------------|--------------
0.1 mm	| 0.38 arcmin
0.25 mm	| 0.94 arcmin
0.5 mm	| 1.88 arcmin
1 mm	| 3.76 arcmin
2 mm	| 7.52 arcmin
5 mm	18.8 arcmin

For comparison, conventional 20/20 visual acuity corresponds to resolving approximately 1 arcminute of angular separation under favorable, high-contrast conditions.

However, this represents a threshold for simple features under controlled conditions, not a practical target for complex scientific diagrams.

A feature subtending 1 arcminute may technically be resolvable but still be difficult to see reliably.
### 2. Feature visibility involves more than size
Four attributes are especially important:
Attribute	What it measures	Why it matters
Angular feature size	Apparent size of lines, symbols, and details	Small details become unresolvable
Local contrast	Difference between a feature and its surrounding background	Low-contrast features need to be larger
Spatial frequency	Density of repeating details, edges, and patterns	Fine structures merge together
Visual crowding	Proximity of neighboring features	Separately resolvable features can become difficult to distinguish

For example, a thin black line against white may be visible at a smaller angular size than a light-gray line against white. Two thin parallel lines also require sufficient separation to remain distinguishable.

A useful evaluator therefore needs to analyze the image itself, not simply its dimensions.
### 3. A practical algorithm for diagram evaluation
Suppose you have:
- A raster image of width \(W\) and height \(H\), in pixels.
- A proposed display width \(w\) and height \(h\), in millimeters.
- A viewing distance \(d\), in millimeters.
You can calculate how much visual detail survives when the image is viewed at the proposed size.
Stage A: Convert pixels into visual angles
For an image displayed without distortion:
\[
\text{mm per pixel}=\frac{w}{W}=\frac{h}{H}
\]

Therefore:
\[
\text{arcmin per pixel}
\approx3438\frac{w}{Wd}
\]

This conversion lets you express every detected image feature in angular units.
Stage B: Analyze the diagram's features
Use computer vision to identify:
1. Line widths, including the thinnest important lines.
2. Spacing between adjacent lines and structures.
3. Text heights and stroke widths.
4. Small symbols, markers, and graphical elements.
5. Local luminance contrast.
6. Dense or highly detailed regions.
OpenCV can provide much of this information using edge detection, distance transforms, connected-component analysis, and local contrast measurements.

Unlike a global edge-density metric, these measurements capture the actual size and separation of features.
Stage C: Apply a perceptual visibility model
A useful approach is to use a contrast sensitivity function (CSF), which models the human visual system's sensitivity to different spatial frequencies.

Contrast sensitivity is not constant: the eye is generally more sensitive to intermediate spatial frequencies than to extremely fine detail. 
### 4. Contrast sensitivity and spatial frequency
NCBI+1


A simplified model would be:
$$
V_i=\frac{C_i}{C_{\text{threshold}}(f_i)}
$$

Where:
- $V_i$ is the visibility margin of feature \(i\).
- $C_i$ is its measured contrast.
- $f_i$ is its angular spatial frequency.
- $C_{\text{threshold}}$ is the contrast needed to detect that frequency.
A visibility margin greater than 1 suggests that a feature exceeds the modeled detection threshold.

This is still a detection model, not a guarantee of recognition. A detailed model should also account for crowding, feature importance, viewing conditions, and the age or acuity of the intended audience.
### 5. Calculate the minimum acceptable diagram size
For the poster-layout optimizer, Instead of evaluating a diagram only at its current dimensions, determine its minimum acceptable display dimensions at 3 feet.

Let:
$$
V(w,h,d)
$$

represent a perceptual legibility score obtained by analyzing the diagram at physical size $(w \times h)$ and distance $d$.

You can then search for the smallest scale factor \(k\) such that:
$$
V(kw_0,kh_0,d)\ge V_{\min}
$$

This is a one-dimensional search because the diagram's aspect ratio remains fixed.

A binary search is usually sufficient, provided the chosen visibility score is approximately monotonic with increasing size.
Example: minimum feature size at 3 feet
An ISO ergonomics technical report references a minimum character or symbol visual angle of 20 arcminutes for legibility. That corresponds to roughly 5.3 mm at 914.4 mm viewing distance. It is a useful reference for labels and symbols, but not a universal requirement for every graphical feature.