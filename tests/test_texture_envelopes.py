"""Constant LWO2 texture envelopes: preserve native bytes and reject unsafe curves."""
import unittest
import test_textures as tx
from test_converter import U16, F32, chunk, form, vx, s0
import test_gltf as gltf


def envelope(index, value, component=0, *, keys=None, shape=b"LINE", pre=5, post=5, extra=b""):
    if keys is None:
        keys = [(0, value), (1, value)]
    records = b"".join(chunk("KEY ", F32(time, v), True) + chunk("SPAN", shape, True)
                       for time, v in keys)
    return chunk("ENVL", vx(index) + chunk("TYPE", U16(0x0300 + component), True)
                 + chunk("PRE ", U16(pre), True) + records + chunk("POST", U16(post), True) + extra)


def mapped(tag, values, reference):
    projection = chunk("PROJ", U16(0), True) + chunk("AXIS", U16(2), True)
    data = F32(*values) + reference
    if tag == "OPAC":
        block = tx.imap(header=chunk(tag, U16(0) + data, True), extra=projection)
    elif tag in ("CNTR", "SIZE", "ROTA", "FALL"):
        if tag == "FALL":
            data = U16(0) + data
        block = tx.imap(extra=projection + chunk("TMAP", chunk(tag, data, True), True))
    else:
        block = tx.imap(extra=projection + chunk(tag, data, True))
    return tx.uv_textured(block)


class TextureEnvelopeTests(unittest.TestCase):
    setUp = tx.TextureTests.setUp
    tearDown = tx.TextureTests.tearDown
    write = tx.TextureTests.write
    run_cli = tx.TextureTests.run_cli
    convert = tx.TextureTests.convert
    object_data = tx.TextureTests.object_data

    def export(self, raw):
        self.write("maps/screen.iff", tx.ilbm([[(255, 0, 0), (0, 255, 0)], [(0, 0, 255), (255, 255, 0)]]))
        out, manifest = self.convert(self.write("envelopes.lwo", raw), code=2)
        directory, native = self.object_data(out, manifest)
        self.assertEqual((directory / "source.bin").read_bytes(), raw)
        return out, manifest, native["materials"][0]

    def assert_same_as_static(self, tag, values, refs, envs):
        static, static_manifest, static_material = self.export(mapped(tag, values, vx(0)))
        raw = mapped(tag, values, refs)
        raw = form("LWO2", raw[12:], *envs)
        out, manifest, material = self.export(raw)
        binding = material["textures"][0]
        self.assertTrue(binding["lwo2_block"]["has_envelopes"])
        self.assertTrue(binding["lwo2_block"]["constant_envelopes_at_native_values"])
        self.assertEqual(binding["export_status"], "approximated")
        self.assertEqual(material["derived_map_sha256"], static_material["derived_map_sha256"])
        self.assertIn("base_color", material["derived_maps"])
        # Compare all geometry/UV bytes against an envelope-free control.
        self.assertEqual((out / manifest["assets"][0]["gltf_bin"]).read_bytes(),
                         (static / static_manifest["assets"][0]["gltf_bin"]).read_bytes())
        data, _ = gltf.load(out / manifest["assets"][0]["gltf"])
        self.assertIn("baseColorTexture", data["materials"][0]["pbrMetallicRoughness"])
        self.assertIn("map_Kd ", (out / manifest["assets"][0]["mtl"]).read_text())

    def test_constant_vectors_match_static_mapping_with_both_index_encodings(self):
        fields = [("CNTR", (.125, .25, .5), 1), ("SIZE", (2, 3, 4), 7),
                  ("ROTA", (.1, .2, .3), 4), ("FALL", (0, 0, 0), 13)]
        for tag, values, component in fields:
            for base in (1, 0x10000):
                for redundant in (False, True):
                    with self.subTest(tag=tag, base=base, redundant=redundant):
                        refs = vx(base) + (b"".join(vx(base + i) for i in range(3)) if redundant else b"")
                        envs = [envelope(base + i, v, component + i) for i, v in enumerate(values)]
                        self.assert_same_as_static(tag, values, refs, envs)

    def test_constant_scalars_and_outside_behaviors(self):
        for tag, value in (("OPAC", 1), ("WRPW", 2), ("WRPH", 3), ("TAMP", .5)):
            for behavior in range(1, 6):
                with self.subTest(tag=tag, behavior=behavior):
                    self.assert_same_as_static(tag, (value,), vx(7),
                                               [envelope(7, value, pre=behavior, post=behavior)])
        self.assert_same_as_static("TAMP", (0,), vx(7), [envelope(7, 0, pre=0, post=0)])
        self.assert_same_as_static("TAMP", (.5,), vx(7), [envelope(7, .5, shape=b"STEP")])

    def test_changing_missing_duplicate_and_untyped_vector_components_are_blocked(self):
        values = (.125, .25, .5)
        good = [envelope(i + 1, v, i + 1) for i, v in enumerate(values)]
        cases = [("missing Y", good[:1] + good[2:]), ("duplicate X", good + good[:1]),
                 ("wrong component type", [envelope(1, values[0], 7)] + good[1:])]
        for i, v in enumerate(values):
            cases.append((f"animated component {i}", good[:i] +
                          [envelope(i + 1, v, i + 1, keys=[(0, v), (1, v + 1)])] + good[i + 1:]))
        for name, envs in cases:
            with self.subTest(name=name):
                raw = mapped("CNTR", values, vx(1) + vx(1) + vx(2) + vx(3))
                self.assert_blocked(form("LWO2", *envs, raw[12:]))
        for refs in (vx(1) + vx(3) + vx(2) + vx(1), vx(1) + b"\xff"):
            raw = mapped("CNTR", values, refs)
            self.assert_blocked(form("LWO2", raw[12:], *good))

    def assert_blocked(self, raw):
        out, manifest, material = self.export(raw)
        binding = material["textures"][0]
        self.assertTrue(binding["lwo2_block"]["has_envelopes"])
        self.assertFalse(binding["lwo2_block"]["constant_envelopes_at_native_values"])
        self.assertEqual(material["derived_maps"], {})
        self.assertIn("envelopes require evaluation", binding["issue"])
        data, _ = gltf.load(out / manifest["assets"][0]["gltf"])
        self.assertNotIn("baseColorTexture", data["materials"][0]["pbrMetallicRoughness"])

    def test_equal_endpoints_do_not_hide_unsupported_or_malformed_envelopes(self):
        cases = [envelope(7, .75),  # constant differs from the native value
                 envelope(7, .5, shape=b"HERM" + F32(1, -1)),
                 envelope(7, .5, shape=b"TCB " + F32(0, 0, 0)),
                 envelope(7, .5, pre=0), envelope(7, .5, post=0), envelope(7, .5, post=6),
                 envelope(7, .5, keys=[(1, .5), (0, .5)]),
                 envelope(7, .5, keys=[(0, .5), (0, .5)]),
                 envelope(7, .5, keys=[(0, .5)]),
                 envelope(7, .5, keys=[(0, .5), (1, float("nan"))]),
                 envelope(7, .5, extra=chunk("CHAN", s0("modifier") + U16(0), True)),
                 envelope(7, .5, extra=chunk("CHAN", s0("modifier") + U16(1), True)),
                 envelope(7, .5, extra=chunk("PRE ", U16(5), True)),
                 envelope(7, .5, extra=chunk("TEST", b"", True)),
                 chunk("ENVL", vx(7) + b"KEY " + U16(8) + F32(0))]
        raw = mapped("TAMP", (.5,), vx(7))
        for index, env in enumerate(cases):
            with self.subTest(case=index):
                self.assert_blocked(form("LWO2", raw[12:], env))

    def test_preset_envelopes_only_bind_within_the_embedded_object(self):
        raw = mapped("TAMP", (.5,), vx(7))
        raw = form("LWO2", raw[12:], envelope(7, .5))
        preset = form("PST_", envelope(7, .75), chunk("PDAT", raw), envelope(7, .25))
        _, _, material = self.export(preset)
        self.assertTrue(material["textures"][0]["lwo2_block"]["constant_envelopes_at_native_values"])
        self.assertIn("base_color", material["derived_maps"])


if __name__ == "__main__":
    unittest.main()
