"""Behavioral coverage for the static build and its failure boundaries."""

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import tempfile
import unittest
from xml.etree import ElementTree
from urllib.parse import parse_qs, urlsplit


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_site.py"
SPEC = importlib.util.spec_from_file_location("build_site", SCRIPT)
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


class BuildSiteTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "site"
        self.root.mkdir()
        self.config = {
            "siteUrl": "https://ozecaoo.com/", "siteName": "oZECAOo",
            "siteTitle": "oZECAOo | Jogos, gameplays e lives", "siteDescription": "Site oficial do Zecão: jogos e canais oficiais.",
            "language": "pt-BR", "locale": "pt_BR", "themeColor": "#0b0b0e",
            "creator": {"name": "Zecão", "brandName": "ZECAO", "handle": "@ozecaoo", "alternateNames": ["ZECAO", "oZECAOo", "@ozecaoo"], "description": "YouTube, Instagram e Twitch.", "heroDescription": "Vídeos e lives de jogos.", "image": "assets/avatar.webp"},
            "socialImage": {"path": "assets/social-preview-v1.jpg", "type": "image/jpeg", "width": 1200, "height": 630, "alt": "Avatar e logo ZECAO"},
            "youtube": "https://www.youtube.com/channel/UCkS1cj-1C9ahgYVvFGz86qw", "youtubeChannelId": "UCkS1cj-1C9ahgYVvFGz86qw",
            "instagram": "https://www.instagram.com/ozecaoo/", "twitch": "https://www.twitch.tv/ozecaoo", "discord": "",
            "trailer": "https://youtu.be/ItDJkbW2M4A", "trailerVideoId": "ItDJkbW2M4A", "videosFeedPath": "data/videos.json", "maxFeedVideos": 8, "maxVisibleVideos": 4,
            "fallbackVideos": [{"id": "EpEauQtpjZ4", "title": "Vídeo de reserva", "published": "2026-10-10T01:00:00Z", "type": "NO YOUTUBE"}],
        }
        self.write_config()
        (self.root / "assets" / "icons").mkdir(parents=True)
        for asset in ("avatar.webp", "social-preview-v1.jpg", "hero.webp", "icons/icon-192x192.png", "icons/icon-512x512.png"):
            (self.root / "assets" / asset).write_bytes(b"fixture")
        for filename in builder.STATIC_FILES:
            (self.root / filename).write_text("", encoding="utf-8")
        (self.root / "index.html").write_text('''<!doctype html><html lang="{{language}}"><head>
<!-- site:seo:start --><!-- site:seo:end -->
<link rel="manifest" href="site.webmanifest"><link rel="stylesheet" href="styles.css"><link rel="icon" href="favicon.ico">
</head><body><h1>{{creator_name}} {{brand_name}} {{handle}}</h1><p>{{hero_description}}</p><p>{{creator_description}}</p>
<img src="{{creator_image}}" alt="{{site_name}}"><a href="{{youtube}}">YouTube</a><a href="{{youtube_videos}}">Vídeos</a><a href="{{instagram}}">Instagram</a><a href="{{twitch}}">Twitch</a><a href="{{trailer}}">Trailer</a>
<div id="videos-list"><!-- site:videos:start --><!-- site:videos:end --></div>
<div data-instagram-embed hidden><iframe data-src="{{instagram_embed}}" title="Instagram"></iframe></div>
<script src="navigation.js"></script><script src="instagram.js" defer></script><script src="config.js"></script><script src="script.js"></script></body></html>''', encoding="utf-8")
        (self.root / "data").mkdir()
        self.write_feed([self.video(1), self.video(2)])

    def write_config(self):
        (self.root / "site.config.json").write_text(json.dumps(self.config), encoding="utf-8")

    def write_feed(self, videos, **extra):
        feed = {"source": "youtube_atom", "channel_id": self.config["youtubeChannelId"], "updated_at": "2026-10-16T00:00:00Z", "videos": videos, **extra}
        (self.root / "data" / "videos.json").write_text(json.dumps(feed), encoding="utf-8")

    @staticmethod
    def video(index):
        return {"id": f"video{index:06d}", "title": f"Vídeo {index}", "published": "2026-10-10T01:00:00Z", "type": "NO YOUTUBE"}

    def build(self, **kwargs):
        return builder.build_site(self.root, **kwargs)

    @staticmethod
    def tree(directory):
        return {str(path.relative_to(directory)): path.read_bytes() for path in directory.rglob("*") if path.is_file()}

    def read_html(self):
        return (self.root / "dist" / "index.html").read_text(encoding="utf-8")

    def asset_versions(self):
        parser = builder.ReferenceParser()
        parser.feed(self.read_html())
        return {urlsplit(reference).path: parse_qs(urlsplit(reference).query)["v"][0] for reference in parser.references if urlsplit(reference).path in builder.VERSIONED_FILES}

    def test_static_page_has_identity_cards_seo_and_all_public_dependencies(self):
        before = self.tree(self.root)
        output = self.build()
        after = {name: value for name, value in self.tree(self.root).items() if not name.startswith("dist/")}
        self.assertEqual(before, after, "Build must never modify source files")
        page = self.read_html()
        self.assertNotIn("{{", page)
        self.assertIn("Zecão ZECAO @ozecaoo", page)
        self.assertIn('<link rel="canonical" href="https://ozecaoo.com/"', page)
        self.assertIn('name="twitter:card" content="summary_large_image"', page)
        self.assertIn('property="og:image" content="https://ozecaoo.com/assets/social-preview-v1.jpg"', page)
        self.assertIn('data-src="https://www.instagram.com/ozecaoo/embed/"', page)
        self.assertTrue((output / "instagram.js").exists())
        self.assertEqual(page.count('class="video-tile"'), 2)
        self.assertIn("09/10/2026", page, "Published date must use São Paulo timezone")
        self.assertIn("/vi_webp/video000001/maxresdefault.webp", page)
        self.assertIn('width="1280" height="720" loading="lazy"', page)
        structured = json.loads(re.search(r'<script type="application/ld\+json">(.*?)</script>', page, re.S).group(1))
        nodes = structured["@graph"]
        self.assertEqual([node["@type"] for node in nodes], ["WebSite", "ProfilePage", "Person"])
        self.assertEqual(nodes[1]["mainEntity"]["@id"], nodes[2]["@id"])
        self.assertEqual(nodes[0]["publisher"]["@id"], nodes[2]["@id"])
        self.assertEqual(nodes[2]["sameAs"], [self.config[key] for key in ("youtube", "instagram", "twitch")])
        robots = (output / "robots.txt").read_text()
        for agent in ("*", "Googlebot", "OAI-SearchBot", "GPTBot", "Google-Extended"):
            self.assertIn(f"User-agent: {agent}\nAllow: /", robots)
        self.assertIn("Sitemap: https://ozecaoo.com/sitemap.xml", robots)
        sitemap = ElementTree.parse(output / "sitemap.xml").getroot()
        self.assertEqual([element.text for element in sitemap.iter() if element.tag.endswith("}loc")], ["https://ozecaoo.com/"])
        self.assertEqual(len(list(sitemap.iter())), 3)
        manifest = json.loads((output / "site.webmanifest").read_text())
        self.assertEqual(manifest["name"], self.config["siteTitle"])
        self.assertEqual([manifest[key] for key in ("id", "scope", "start_url")], ["./"] * 3)
        self.assertEqual(manifest["display"], "browser")
        runtime = (output / "config.js").read_text()
        self.assertIn("window.ZECAO_CONFIG = ", runtime)
        self.assertIn('"maxVisibleVideos": 4', runtime)
        self.assertFalse((output / "site.config.json").exists())
        self.assertFalse((output / "llms.txt").exists())
        self.assertFalse((output / "CNAME").exists())

    def test_feed_retains_eight_valid_unique_videos_and_renders_four_without_trailer(self):
        trailer = {**self.video(1), "id": self.config["trailerVideoId"]}
        self.write_feed([None, {"id": "invalid"}, trailer, self.video(2), self.video(2), *[self.video(index) for index in range(3, 15)]])
        output = self.build()
        feed = json.loads((output / "data" / "videos.json").read_text())
        self.assertEqual(len(feed["videos"]), 8)
        page = self.read_html()
        self.assertEqual(page.count('class="video-tile"'), 4)
        self.assertNotIn(f'data-video-id="{self.config["trailerVideoId"]}"', page)
        self.assertIn('data-video-id="video000005"', page)
        self.assertNotIn('data-video-id="video000006"', page)

    def test_missing_malformed_empty_foreign_and_invalid_feeds_use_valid_reserve(self):
        feed_path = self.root / "data" / "videos.json"
        cases = [None, "{", "[]", "{}", '{"videos": []}', json.dumps({"videos": [{"id": "../../bad"}]}), json.dumps({"videos": [self.video(3)], "channel_id": "other-channel"})]
        for feed_text in cases:
            with self.subTest(feed=feed_text):
                if feed_text is None:
                    feed_path.unlink(missing_ok=True)
                else:
                    feed_path.write_text(feed_text)
                output = self.build()
                feed = json.loads((output / "data" / "videos.json").read_text())
                self.assertEqual(feed["source"], "configured_fallback")
                self.assertEqual(feed["videos"][0]["id"], "EpEauQtpjZ4")
                self.assertIn("Vídeo de reserva", self.read_html())

    def test_untrusted_titles_and_jsonld_strings_are_escaped(self):
        attack = '</script><script>alert("x")</script>& Zecão'
        self.config["creator"]["description"] = attack
        self.config["creator"]["alternateNames"].append(attack)
        self.config["fallbackVideos"][0]["title"] = attack
        self.write_config()
        self.write_feed([{**self.video(1), "title": attack}])
        self.build()
        page = self.read_html()
        self.assertNotIn('<script>alert("x")', page)
        self.assertIn("&lt;/script&gt;&lt;script&gt;", page)
        self.assertIn("\\u003c/script\\u003e", page)
        structured = json.loads(re.search(r'<script type="application/ld\+json">(.*?)</script>', page, re.S).group(1))
        self.assertEqual(structured["@graph"][2]["description"], attack)
        self.assertIn("\\u003c/script\\u003e", (self.root / "dist" / "config.js").read_text())

    def test_identical_content_ignores_poll_timestamp_and_old_output_files(self):
        output = self.build()
        first = self.tree(output)
        (output / "obsolete.txt").write_text("must disappear")
        self.write_feed([self.video(1), self.video(2)], updated_at="2027-01-01T01:02:03Z")
        self.build()
        self.assertEqual(first, self.tree(output))
        self.assertNotIn("lastmod", (output / "sitemap.xml").read_text())

    def test_css_and_scripts_use_content_versions_without_renaming_assets(self):
        output = self.build()
        versions = self.asset_versions()
        self.assertEqual(set(versions), set(builder.VERSIONED_FILES))
        for filename, version in versions.items():
            with self.subTest(filename=filename):
                self.assertEqual(version, hashlib.sha256((output / filename).read_bytes()).hexdigest()[:12])
                self.assertRegex(version, r"^[a-f0-9]{12}$")
        self.assertIn('href="favicon.ico"', self.read_html())
        self.assertIn('href="site.webmanifest"', self.read_html())
        self.assertIn('src="assets/avatar.webp"', self.read_html())
        # Source markup stays useful for editing and never receives build hashes.
        self.assertIn('href="styles.css"', (self.root / "index.html").read_text())

    def test_css_change_updates_its_version_without_invalidating_scripts(self):
        self.build()
        previous = self.asset_versions()
        (self.root / "styles.css").write_text(".instagram-section{display:grid}", encoding="utf-8")
        self.build()
        current = self.asset_versions()
        self.assertNotEqual(previous.pop("styles.css"), current.pop("styles.css"))
        self.assertEqual(previous, current)

    def test_runtime_config_changes_invalidate_config_script_only(self):
        self.build()
        previous = self.asset_versions()
        changes = (("maxVisibleVideos", 3), ("fallbackVideos", [{**self.config["fallbackVideos"][0], "title": "Reserva atualizada"}]))
        for key, value in changes:
            with self.subTest(key=key):
                self.config[key] = value
                self.write_config()
                self.build()
                current = self.asset_versions()
                self.assertNotEqual(previous["config.js"], current["config.js"])
                self.assertEqual({name: version for name, version in previous.items() if name != "config.js"}, {name: version for name, version in current.items() if name != "config.js"})
                previous = current

    def test_refreshed_feed_can_be_read_from_dist_before_replacement(self):
        feed_path = self.root / "dist" / "data" / "videos.json"
        feed_path.parent.mkdir(parents=True)
        feed_path.write_text(json.dumps({"videos": [self.video(7)]}))
        self.build(feed_path=feed_path)
        self.assertIn('data-video-id="video000007"', self.read_html())
        self.assertTrue((self.root / "dist" / builder.BUILD_MARKER).exists())

    def test_valid_updater_feed_preserves_its_recognized_provenance(self):
        for source in ("youtube_atom", "configured_fallback"):
            with self.subTest(source=source):
                self.write_feed([self.video(7)], source=source)
                output = self.build()
                feed = json.loads((output / "data" / "videos.json").read_text())
                self.assertEqual(feed["source"], source)
                self.assertEqual(feed["videos"][0]["id"], "video000007")

    def test_unsafe_config_or_output_is_rejected_without_deleting_anything(self):
        original = copy.deepcopy(self.config)
        for key, value in (("siteUrl", "http://ozecaoo.com/"), ("siteUrl", "https://ozecaoo.com/wrong/"), ("youtube", "javascript:alert(1)"), ("instagram", "https://example.com/ozecaoo/"), ("instagram", "https://www.instagram.com/p/some-post/"), ("instagram", "https://www.instagram.com/ozecaoo/?next=other"), ("maxFeedVideos", 300), ("trailerVideoId", "../unsafe")):
            with self.subTest(key=key, value=value):
                self.config = {**copy.deepcopy(original), key: value}
                self.write_config()
                with self.assertRaises(builder.BuildError):
                    self.build()
        self.config = copy.deepcopy(original)
        self.config["creator"]["image"] = "assets/../site.config.json"
        self.write_config()
        with self.assertRaises(builder.BuildError):
            self.build()
        self.config = original
        self.write_config()
        for output in (self.root, self.root.parent, self.root / "assets", self.root / "data" / "output"):
            with self.subTest(output=output):
                with self.assertRaises(builder.BuildError):
                    self.build(output=output)
        unowned = self.root.parent / "other-work"
        unowned.mkdir()
        keep = unowned / "keep.txt"
        keep.write_text("preserve")
        with self.assertRaises(builder.BuildError):
            self.build(output=unowned)
        self.assertEqual(keep.read_text(), "preserve")
        symlink = self.root.parent / "linked-output"
        symlink.symlink_to(unowned, target_is_directory=True)
        with self.assertRaises(builder.BuildError):
            self.build(output=symlink)
        self.assertEqual(keep.read_text(), "preserve")

    def test_missing_public_asset_or_template_marker_preserves_last_build(self):
        output = self.build()
        previous = self.tree(output)
        (self.root / "assets" / "avatar.webp").unlink()
        with self.assertRaises(builder.BuildError):
            self.build()
        self.assertEqual(previous, self.tree(output))
        template = self.root / "index.html"
        template.write_text(template.read_text().replace("<!-- site:videos:start -->", ""))
        with self.assertRaises(builder.BuildError):
            self.build()
        self.assertEqual(previous, self.tree(output))


if __name__ == "__main__":
    unittest.main()
