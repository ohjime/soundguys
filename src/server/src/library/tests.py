import ast
from pathlib import Path

from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from core.models import Sound


class LibraryViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.sound = Sound.objects.create(
            file="sounds/test-sound.wav",
            title="Forest Rain",
            embeddings=[0, 0, 0, 0, 0],
            flavor="Gentle rain in a pine forest",
        )
        cls.sound.tags.add("rain")
        titles = ("River Current", "Prairie Wind", "Morning Birds")
        for index, title in enumerate(titles, start=1):
            Sound.objects.create(
                file=f"sounds/library-{index}.wav",
                title=title,
                embeddings=[0, 0, 0, 0, 0],
                flavor=f"Featured recording {index}",
            )

    def test_library_index_page_renders_shell(self):
        response = self.client.get(reverse("library:index"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "content-loader")

    def test_library_initial_requires_htmx(self):
        response = self.client.get(reverse("library:initial"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Request Denied.")

    def test_library_initial_fragment_renders_sounds(self):
        response = self.client.get(
            reverse("library:initial"), HTTP_HX_REQUEST="true"
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sound Library")
        self.assertContains(response, "Forest Rain")
        self.assertContains(response, "Gentle rain in a pine forest")

    def test_library_initial_renders_timed_primary_and_secondary_content(self):
        response = self.client.get(
            reverse("library:initial"), HTTP_HX_REQUEST="true"
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data-core-tab-body")
        self.assertContains(response, 'data-reveal-mode="timer"')
        self.assertContains(response, 'data-reveal-delay="1400"')
        self.assertContains(response, "data-tab-primary", count=1)
        self.assertContains(response, "data-tab-secondary", count=1)
        self.assertContains(response, 'data-library-section="primary"')
        self.assertContains(response, 'data-library-section="secondary"')
        self.assertContains(response, "Featured sounds")
        self.assertContains(response, "Extended Sound Collection")
        self.assertContains(response, "Contribute a recording")
        self.assertContains(response, "Explore Full Sound Collection ↓")
        content = response.content.decode()
        self.assertLess(
            content.index("data-tab-primary"), content.index("Featured sounds")
        )
        self.assertLess(
            content.index("Featured sounds"), content.index("data-tab-secondary")
        )
        self.assertLess(
            content.index("data-tab-secondary"),
            content.index("Extended Sound Collection"),
        )
        self.assertLess(
            content.index("Extended Sound Collection"), content.index("Forest Rain")
        )


class LibraryArchitectureTests(SimpleTestCase):
    """Enforce architectural isolation: library can only depend on core and itself."""

    FORBIDDEN_APP_DEPENDENCIES = {
        "app",
        "featured",
        "login",
        "mixer",
        "profile",
        "studio",
        "vote",
    }

    def test_library_only_depends_on_core(self):
        library_dir = Path(__file__).resolve().parent
        for py_file in library_dir.glob("*.py"):
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        top_module = alias.name.split(".")[0]
                        self.assertNotIn(
                            top_module,
                            self.FORBIDDEN_APP_DEPENDENCIES,
                            f"{py_file.name} imports forbidden feature app '{top_module}'",
                        )
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        top_module = node.module.split(".")[0]
                        self.assertNotIn(
                            top_module,
                            self.FORBIDDEN_APP_DEPENDENCIES,
                            f"{py_file.name} imports from forbidden feature app '{top_module}'",
                        )
