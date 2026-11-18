import io
import unittest
from unittest.mock import AsyncMock, patch

from PIL import Image
import pypdfium2

import content
import graphics
import poster_rendering


class GraphicRenderingTests(unittest.IsolatedAsyncioTestCase):
    async def test_document_without_raster_graphics_is_unchanged(self) -> None:
        markup = "<html><body>Poster</body></html>"
        self.assertEqual(await graphics.embed_sources(markup, 7, 11), markup)

    async def test_source_is_embedded_for_pdf_without_a_browser_session(self) -> None:
        image = io.BytesIO()
        Image.new("RGB", (50, 50), "red").save(image, format="PNG")
        store = AsyncMock()
        store.graphic_source.return_value = (image.getvalue(), "image/png")
        graphic = graphics.clean_section(
            '<span class="graphic" data-sid="22" data-type="png" data-width="50"></span>',
            sources=[content.GraphicSource(22, "chart.png")], section="results",
        )
        rendered = (
            '<html><head><style>.graphic img { width: 50px; height: 50px; }</style></head>'
            '<body><article class="poster-document" data-width="200" data-height="200">'
            + graphic + "</article></body></html>"
        )
        with patch.object(content, "store", return_value=store):
            embedded = await graphics.embed_sources(rendered, 7, 11)
        store.graphic_source.assert_awaited_once_with(7, 11, 22)
        self.assertIn("data:image/png;base64,", embedded)
        pdf = await poster_rendering.poster_pdf(embedded)
        document = pypdfium2.PdfDocument(pdf)
        try:
            page = document[0]
            try:
                raster = page.render(scale=1).to_pil().convert("RGB")
                self.assertGreater(sum(
                    1 for red, green, blue in raster.get_flattened_data()
                    if red > 200 and green < 50 and blue < 50
                ), 200)
            finally:
                page.close()
        finally:
            document.close()

    async def test_missing_source_prevents_rendering(self) -> None:
        store = AsyncMock()
        store.graphic_source.return_value = None
        markup = ('<html><body><span class="graphic"><img src="/layout/source?sid=22">'
                  '</span></body></html>')
        with patch.object(content, "store", return_value=store), self.assertRaises(ValueError):
            await graphics.embed_sources(markup, 7, 11)
