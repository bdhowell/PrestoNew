import json
import unittest
from pathlib import Path

from playwright.sync_api import sync_playwright

import layout
import posters
from tests.test_layout import DRAFT

ROOT = Path(__file__).resolve().parent.parent


class TemplateDialogTests(unittest.TestCase):
    conference_details = {}

    def setUp(self) -> None:
        playwright = sync_playwright().start()
        self.addCleanup(playwright.stop)
        browser = playwright.chromium.launch()
        self.addCleanup(browser.close)
        self.page = browser.new_page(viewport={"width": 1000, "height": 900})
        self.page.set_default_timeout(5000)
        self.page.route(
            "http://poster.test/**",
            lambda route: route.fulfill(content_type="text/html", body=""),
        )
        self.page.goto("http://poster.test/")
        self.page.unroute("http://poster.test/**")
        footer = layout.templates.env.get_template("layoutfoot.html").render(
            poster=DRAFT, layout_template="central", poster_presets=posters.POSTER_PRESETS,
            themes=[], font_sets=[],
            poster_preset_errors=layout.DimensionRules.from_details(
                "Meeting" if self.conference_details else "", self.conference_details,
            ).preset_errors(),
        )
        css = (ROOT / "static/css/layout-template.css").read_text()
        self.page.set_content(f"<style>* {{box-sizing: border-box}}</style><style>{css}</style>{footer}")
        self.page.evaluate("""() => {
            window.wikkitMessages = [];
            window.wikkit = {say: message => window.wikkitMessages.push(message)};
        }""")
        self.page.locator("#appearance-controls").evaluate("el => el.hidden = false")
        self.page.add_script_tag(path=str(ROOT / "static/javascript/layout-template.js"))
        self.page.locator("#template").click()

    def choose_category(self, category: str) -> None:
        self.page.locator(f"label:has(input[name=category][value={category}])").click()

    def test_dialog_is_capped_at_700_pixels_and_fits_smaller_viewports(self) -> None:
        for width, height in ((1000, 900), (480, 600)):
            self.page.set_viewport_size({"width": width, "height": height})
            self.choose_category("landscape")
            bounds = self.page.locator("#template-dialog").bounding_box()
            self.assertLessEqual(bounds["width"], min(700, width - 32))
            self.assertLessEqual(bounds["height"], min(700, height - 32))
            self.assertGreaterEqual(bounds["x"], 0)
            self.assertGreaterEqual(bounds["y"], 0)
            self.page.locator("#template-cancel").click()
            self.assertFalse(self.page.locator("#template-dialog").is_visible())
            self.page.locator("#template").click()

    def test_dialog_height_and_position_stay_constant_across_categories(self) -> None:
        self.page.add_style_tag(path=str(ROOT / "static/css/layout.css"))
        for width, height in ((1000, 900), (480, 600)):
            self.page.set_viewport_size({"width": width, "height": height})
            initial = self.page.locator("#template-dialog").bounding_box()
            for category in ("square", "landscape", "portrait", "custom", "landscape"):
                self.choose_category(category)
                bounds = self.page.locator("#template-dialog").bounding_box()
                self.assertAlmostEqual(bounds["height"], min(700, height - 32), delta=1)
                self.assertEqual(bounds, initial)

    def test_action_buttons_keep_constant_height_across_categories(self) -> None:
        for width, height in ((1000, 900), (480, 600)):
            self.page.set_viewport_size({"width": width, "height": height})
            for category in ("custom", "square", "landscape", "portrait"):
                self.choose_category(category)
                for button in ("#template-cancel", "#template-apply"):
                    self.assertAlmostEqual(
                        self.page.locator(button).bounding_box()["height"], 44, delta=0.1,
                    )

    def test_controls_and_actions_stay_anchored_across_categories_and_units(self) -> None:
        self.page.add_style_tag(path=str(ROOT / "static/css/layout.css"))
        selectors = (
            "#template-dialog-title", "#template-units", "#template-categories",
            "#template-options", "#template-cancel", "#template-apply",
        )
        for width, height in ((1000, 900), (700, 700), (480, 600)):
            self.page.set_viewport_size({"width": width, "height": height})
            initial = {selector: self.page.locator(selector).bounding_box() for selector in selectors}
            for units in ("in", "cm", "mm"):
                self.page.locator("#template-units-select").select_option(units)
                for category in ("square", "landscape", "portrait", "custom"):
                    with self.subTest(viewport=(width, height), units=units, category=category):
                        self.choose_category(category)
                        for selector in selectors:
                            self.assertEqual(self.page.locator(selector).bounding_box(), initial[selector])
                        dialog = self.page.locator("#template-dialog").bounding_box()
                        actions = self.page.locator(".template-dialog-actions").bounding_box()
                        self.assertAlmostEqual(
                            dialog["y"] + dialog["height"] - actions["y"] - actions["height"],
                            25, delta=1,
                        )
                        self.assertTrue(self.page.locator("#template-form").evaluate(
                            "el => el.scrollHeight <= el.clientHeight + 1",
                        ))
                        self.assertEqual(
                            self.page.locator(".template-custom-dimensions").is_visible(),
                            category == "custom",
                        )
                        self.assertEqual(
                            self.page.locator(".template-size-options").is_visible(),
                            category != "custom",
                        )

    def test_initialization_and_orientation_filtered_unit_catalogs(self) -> None:
        self.assertEqual(self.page.locator("#template-units-select").input_value(), "in")
        self.assertEqual(self.page.locator("input[name=template]:checked").input_value(), "central")
        self.assertEqual(self.page.locator("input[name=category]:checked").input_value(), "custom")
        self.choose_category("portrait")
        self.assertEqual(self.page.locator(".template-size-shape").count(), 9)
        self.choose_category("landscape")
        self.assertEqual(self.page.locator(".template-size-shape").first.text_content(), '40" x 30"')
        self.page.locator("#template-units-select").select_option("cm")
        self.assertEqual(self.page.locator(".template-size-shape").all_text_contents(), [
            "ISO A1", "100 x 70 cm", "ISO A0", "122 x 91 cm",
            "140 x 100 cm", "200 x 100 cm",
        ])
        self.page.locator("#template-units-select").select_option("mm")
        self.assertEqual(self.page.locator(".template-size-shape").all_text_contents(), ["ISO A1", "ISO A0"])
        self.choose_category("square")
        self.assertTrue(self.page.locator("#template-apply").is_disabled())
        self.assertIn("No presets", self.page.locator("#template-status").text_content())
        self.page.locator("#template-units-select").select_option("cm")
        self.assertEqual(self.page.locator(".template-size-shape").all_text_contents(), ["100 x 100 cm"])
        self.assertTrue(self.page.locator("#template-apply").is_enabled())

    def test_landscape_list_scrolls_without_moving_or_hiding_actions(self) -> None:
        self.page.add_style_tag(path=str(ROOT / "static/css/layout.css"))
        for width, height in ((1000, 900), (700, 700), (480, 600)):
            self.page.set_viewport_size({"width": width, "height": height})
            self.choose_category("landscape")
            form = self.page.locator("#template-form")
            self.assertTrue(form.evaluate("el => el.scrollHeight <= el.clientHeight + 1"))
            dialog = self.page.locator("#template-dialog").bounding_box()
            self.assertLessEqual(dialog["width"], min(700, width - 32))
            self.assertAlmostEqual(dialog["height"], min(700, height - 32), delta=1)
            actions = self.page.locator(".template-dialog-actions").bounding_box()
            self.assertLessEqual(actions["x"] + actions["width"], dialog["x"] + dialog["width"])
            self.assertAlmostEqual(
                actions["y"] + actions["height"], dialog["y"] + dialog["height"] - 25, delta=1,
            )
            for button in ("#template-cancel", "#template-apply"):
                bounds = self.page.locator(button).bounding_box()
                self.assertGreaterEqual(bounds["y"], dialog["y"])
                self.assertLessEqual(bounds["y"] + bounds["height"], dialog["y"] + dialog["height"])
                self.assertLessEqual(bounds["y"] + bounds["height"], height)
                self.assertTrue(self.page.locator(button).evaluate("""el => {
                    const r = el.getBoundingClientRect();
                    return el.contains(document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2));
                }"""))
            scroller = self.page.locator(".template-size-options")
            distance = scroller.evaluate("el => {el.scrollTop = el.scrollHeight; return el.scrollTop}")
            self.assertGreater(distance, 0)
            self.assertEqual(self.page.locator(".template-dialog-actions").bounding_box(), actions)
            self.assertEqual(form.evaluate("el => el.scrollTop"), 0)

    def test_iso_dimensions_payload_scale_and_error_retry(self) -> None:
        self.choose_category("portrait")
        self.page.locator("#template-units-select").select_option("cm")
        self.page.locator(".template-size-option").filter(has_text="ISO A0").click()
        self.assertEqual(self.page.locator("input[name=width]").input_value(), "84.1")
        self.assertEqual(self.page.locator("input[name=height]").input_value(), "118.9")
        shape = self.page.locator(".template-size-shape").filter(has_text="ISO A0")
        bounds = shape.bounding_box()
        self.assertAlmostEqual(bounds["width"], 84.1 / 2.54 * 2, delta=0.1)
        self.assertAlmostEqual(bounds["height"], 118.9 / 2.54 * 2, delta=0.1)
        requests = []

        def respond(route):
            requests.append(route.request.post_data_json)
            route.fulfill(
                status=422, content_type="application/json",
                body=json.dumps({"detail": "Conference size limit."}),
            )

        self.page.route("**/layout/dimensions", respond)
        self.page.locator("#template-apply").click()
        self.page.wait_for_function(
            "document.querySelector('#template-status').textContent.includes('Conference size limit.')",
        )
        self.assertEqual(requests, [{"dimensions": "118.9 cm x 84.1 cm", "template": "central"}])
        self.assertTrue(self.page.locator("#template-units-select").is_enabled())

    def test_portrait_width_groups_alignment_and_horizontal_scrolling(self) -> None:
        self.page.set_viewport_size({"width": 480, "height": 900})
        self.choose_category("portrait")
        groups = self.page.locator(".template-width-group")
        self.assertEqual(self.page.locator(".template-width-label").all_text_contents(), [
            "30in wide", "36in wide", "42in wide", "48in wide",
        ])
        self.assertEqual(groups.evaluate_all(
            "groups => groups.map(group => group.querySelectorAll('.template-size-option').length)",
        ), [1, 3, 3, 2])
        self.assertEqual(groups.evaluate_all(
            "groups => groups.map(group => getComputedStyle(group).borderLeftWidth)",
        ), ["0px", "1px", "1px", "1px"])
        self.assertEqual(groups.nth(1).evaluate(
            "group => getComputedStyle(group).borderLeftColor",
        ), "rgb(0, 0, 0)")
        centers = self.page.locator(".template-size-shape").evaluate_all(
            "tiles => tiles.map(tile => {const r = tile.getBoundingClientRect(); return r.y + r.height / 2})",
        )
        self.assertLess(max(centers) - min(centers), 1)
        captions = self.page.locator(".template-width-label").evaluate_all(
            "labels => labels.map(label => label.getBoundingClientRect().y)",
        )
        self.assertLess(max(captions) - min(captions), 1)
        for group in groups.all():
            tiles = group.locator(".template-width-options").bounding_box()
            caption = group.locator(".template-width-label").bounding_box()
            self.assertAlmostEqual(
                caption["x"] + caption["width"] / 2, tiles["x"] + tiles["width"] / 2, delta=1,
            )
            self.assertGreaterEqual(caption["y"], tiles["y"] + tiles["height"])
        scroller = self.page.locator(".template-size-options")
        self.assertTrue(scroller.evaluate("el => el.scrollWidth > el.clientWidth"))
        before = groups.last.locator(".template-width-label").bounding_box()["x"]
        distance = scroller.evaluate("el => {el.scrollLeft = el.scrollWidth; return el.scrollLeft}")
        after = groups.last.locator(".template-width-label").bounding_box()["x"]
        self.assertGreater(distance, 0)
        self.assertAlmostEqual(before - after, distance, delta=1)
        groups.last.locator(".template-size-option").last.click()
        self.assertEqual(self.page.locator("input[name=width]").input_value(), "48")
        self.assertEqual(self.page.locator("input[name=height]").input_value(), "96")

    def test_portrait_groups_refresh_for_units_and_leave_other_categories_unchanged(self) -> None:
        self.choose_category("portrait")
        self.page.locator("#template-units-select").select_option("cm")
        self.assertEqual(self.page.locator(".template-width-label").all_text_contents(), [
            "59.4cm wide", "70cm wide", "84.1cm wide", "91cm wide", "100cm wide",
        ])
        self.page.locator("#template-units-select").select_option("mm")
        self.assertEqual(self.page.locator(".template-width-label").all_text_contents(), [
            "594mm wide", "841mm wide",
        ])
        self.choose_category("landscape")
        self.assertEqual(self.page.locator(".template-width-group").count(), 0)
        self.assertEqual(self.page.locator(".template-size-options").evaluate(
            "el => getComputedStyle(el).display",
        ), "grid")
        self.choose_category("custom")
        self.assertEqual(self.page.locator(".template-width-group").count(), 0)
        self.assertTrue(self.page.locator(".template-custom-dimensions").is_visible())

    def test_custom_conversion_and_reopening_saved_metric_poster(self) -> None:
        self.page.locator("#template-units-select").select_option("cm")
        self.assertAlmostEqual(float(self.page.locator("input[name=width]").input_value()), 91.44)
        self.assertAlmostEqual(float(self.page.locator("input[name=height]").input_value()), 121.92)
        self.page.locator("#template-cancel").click()
        self.page.locator("#template-dialog").evaluate(
            "el => Object.assign(el.dataset, {width: '84.1', height: '118.9', units: 'cm'})",
        )
        self.page.locator("#template").click()
        self.assertEqual(self.page.locator("#template-units-select").input_value(), "cm")
        self.assertEqual(self.page.locator("input[name=category]:checked").input_value(), "portrait")
        self.assertEqual(
            self.page.locator(".template-size-option:has(input:checked)").text_content(), "ISO A0",
        )

    def test_landscape_height_groups_alignment_and_vertical_scrolling(self) -> None:
        self.page.set_viewport_size({"width": 480, "height": 900})
        self.choose_category("landscape")
        groups = self.page.locator(".template-height-group")
        self.assertEqual(self.page.locator(".template-height-label").all_text_contents(), [
            "30in Hgh", "36in Hgh", "42in Hgh", "48in Hgh",
        ])
        self.assertEqual(groups.evaluate_all(
            "groups => groups.map(group => group.querySelectorAll('.template-size-option').length)",
        ), [1, 3, 3, 2])
        self.assertEqual(groups.evaluate_all(
            "groups => groups.map(group => getComputedStyle(group).borderTopWidth)",
        ), ["0px", "1px", "1px", "1px"])
        self.assertEqual(groups.nth(1).evaluate(
            "group => getComputedStyle(group).borderTopColor",
        ), "rgb(0, 0, 0)")
        for group in groups.all():
            tiles = group.locator(".template-height-options").bounding_box()
            caption = group.locator(".template-height-label").bounding_box()
            self.assertAlmostEqual(
                caption["y"] + caption["height"] / 2, tiles["y"] + tiles["height"] / 2, delta=1,
            )
            self.assertLessEqual(caption["x"] + caption["width"], tiles["x"])
        scroller = self.page.locator(".template-size-options")
        self.assertTrue(scroller.evaluate("el => el.scrollHeight > el.clientHeight"))
        self.assertTrue(scroller.evaluate("el => el.scrollWidth === el.clientWidth"))
        before = groups.last.locator(".template-height-label").bounding_box()["y"]
        distance = scroller.evaluate("el => {el.scrollTop = el.scrollHeight; return el.scrollTop}")
        after = groups.last.locator(".template-height-label").bounding_box()["y"]
        self.assertGreater(distance, 0)
        self.assertAlmostEqual(before - after, distance, delta=1)
        groups.last.locator(".template-size-option").last.click()
        self.assertEqual(self.page.locator("input[name=width]").input_value(), "96")
        self.assertEqual(self.page.locator("input[name=height]").input_value(), "48")
        shape = groups.last.locator(".template-size-shape").last.bounding_box()
        self.assertAlmostEqual(shape["width"], 192, delta=0.1)
        self.assertAlmostEqual(shape["height"], 96, delta=0.1)

    def test_landscape_height_groups_refresh_for_units_and_categories(self) -> None:
        self.choose_category("landscape")
        self.page.locator("#template-units-select").select_option("cm")
        self.assertEqual(self.page.locator(".template-height-label").all_text_contents(), [
            "59.4cm Hgh", "70cm Hgh", "84.1cm Hgh", "91cm Hgh", "100cm Hgh",
        ])
        self.page.locator("#template-units-select").select_option("mm")
        self.assertEqual(self.page.locator(".template-height-label").all_text_contents(), [
            "594mm Hgh", "841mm Hgh",
        ])
        self.choose_category("portrait")
        self.assertEqual(self.page.locator(".template-height-group").count(), 0)
        self.assertEqual(self.page.locator(".template-width-group").count(), 2)
        self.choose_category("square")
        self.assertEqual(self.page.locator(".template-height-group").count(), 0)
        self.choose_category("custom")
        self.assertTrue(self.page.locator(".template-custom-dimensions").is_visible())

    def test_landscape_iso_a1_saves_millimeters_and_redirects(self) -> None:
        self.choose_category("landscape")
        self.page.locator("#template-units-select").select_option("mm")
        self.page.locator(".template-size-option").filter(has_text="ISO A1").click()
        requests = []

        def respond(route):
            requests.append(route.request.post_data_json)
            route.fulfill(
                content_type="application/json",
                body=json.dumps({"ok": True, "redirect": "/layout/edit"}),
            )

        self.page.route("**/layout/dimensions", respond)
        self.page.route(
            "**/layout/edit", lambda route: route.fulfill(content_type="text/html", body="Saved"),
        )
        self.page.locator("#template-apply").click()
        self.page.wait_for_url("http://poster.test/layout/edit")
        self.assertEqual(requests, [{"dimensions": "594 mm x 841 mm", "template": "central"}])


class ConferenceTemplateDialogTests(unittest.TestCase):
    conference_details = {"maximum_poster_width": 122, "poster_units": "cm"}
    setUp = TemplateDialogTests.setUp
    choose_category = TemplateDialogTests.choose_category

    def test_incompatible_landscape_preset_is_disabled_with_reason(self) -> None:
        self.choose_category("landscape")
        option = self.page.locator(".template-size-option").filter(has_text='72" x 42"')
        self.assertTrue(option.locator("input").is_disabled())
        self.assertEqual(option.text_content(), '72" x 42"')
        self.assertIn("The maximum is 48.0315 in.", option.get_attribute("title"))
        self.assertEqual(self.page.locator("#template-status").text_content(), "")
        self.assertEqual(self.page.evaluate("window.wikkitMessages.at(-1)"), (
            "<p>I&rsquo;ve disabled some options because they&rsquo;re too large "
            "for your conference requirements.</p>"
        ))
        self.assertEqual(self.page.locator("input[name=width]").input_value(), "40")
        self.assertTrue(self.page.locator("#template-apply").is_enabled())
        self.page.locator("#template-units-select").select_option("cm")
        option = self.page.locator(".template-size-option").filter(has_text="200 x 100 cm")
        self.assertTrue(option.locator("input").is_disabled())
        self.assertEqual(option.text_content(), "200 x 100 cm")
        self.assertIn("The maximum is 122 cm.", option.get_attribute("title"))
        self.choose_category("portrait")
        self.assertEqual(self.page.locator(".template-size-unavailable").count(), 0)
        self.assertTrue(self.page.locator(
            ".template-size-option input:not(:disabled)",
        ).count() > 0)


class UnavailableTemplateDialogTests(unittest.TestCase):
    conference_details = {"maximum_poster_width": 50, "poster_units": "cm"}
    setUp = TemplateDialogTests.setUp
    choose_category = TemplateDialogTests.choose_category

    def test_all_presets_unavailable_disables_apply_but_allows_custom(self) -> None:
        self.choose_category("landscape")
        self.assertEqual(self.page.locator("input[name=size]:checked").count(), 0)
        self.assertTrue(self.page.locator("#template-apply").is_disabled())
        self.assertIn("No presets", self.page.locator("#template-status").text_content())
        self.choose_category("custom")
        self.assertTrue(self.page.locator("#template-apply").is_enabled())


class DisabledOptionMessageTests(unittest.TestCase):
    conference_details = {"maximum_poster_width": 90, "poster_units": "in"}
    setUp = TemplateDialogTests.setUp
    choose_category = TemplateDialogTests.choose_category

    def test_wikkit_quantities_and_silence_without_disabled_options(self) -> None:
        self.assertEqual(self.page.evaluate("window.wikkitMessages"), [])
        self.choose_category("landscape")
        self.assertEqual(self.page.locator("input[name=size]:disabled").count(), 2)
        self.assertEqual(self.page.evaluate("window.wikkitMessages.at(-1)"), (
            "<p>I&rsquo;ve disabled a couple of options because they&rsquo;re too large "
            "for your conference requirements.</p>"
        ))
        self.choose_category("portrait")
        self.choose_category("custom")
        self.assertEqual(self.page.evaluate("window.wikkitMessages.length"), 1)


class SingleDisabledOptionMessageTests(unittest.TestCase):
    conference_details = {"maximum_poster_width": 46, "poster_units": "in"}
    setUp = TemplateDialogTests.setUp
    choose_category = TemplateDialogTests.choose_category

    def test_wikkit_uses_singular_for_one_disabled_option(self) -> None:
        self.choose_category("square")
        self.assertEqual(self.page.locator("input[name=size]:disabled").count(), 1)
        self.assertEqual(self.page.evaluate("window.wikkitMessages.at(-1)"), (
            "<p>I&rsquo;ve disabled one option because they&rsquo;re too large "
            "for your conference requirements.</p>"
        ))
