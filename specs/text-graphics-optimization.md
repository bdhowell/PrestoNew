We're discussing web content layout strategies for scientific presentation posters.

Consider an arbitrary rectangular contentEditable container in which there is a single rectangular graphic. The graphic must be clearly discernible at at a distance of three feet and fit within the container. The user is allowed to position the graphic float left, float right, or be centered with text above and/or below it, in all cases with a margin of 2rem except on the side next to the container when floating.

The container can be up to 40% empty and cannot overflow. (Attempting to cause an overflow will result in the user being notified.) The text is a fixed size and cannot be changed. However, the length of the text can be rewritten longer or shorter, and the graphic can be enlarged or shrunk, so long as it is still discernible at three feet distance.

The `poster_optimizer.py` module provides an algorithm to optimize the layout of such a container. It adjusts the graphic size and text length to maximize the utilization of the container while ensuring the graphic remains discernible at three feet and the container does not overflow. The optimizer respects the user's chosen graphic position and the fixed text size, and it enforces the 2rem margin rule.

Its logic needs to be merged into `magic.py`. to handle sections, whether they are text only, or text and graphics and triggered by an "Auto-fit" click.
