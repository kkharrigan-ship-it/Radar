import unittest

from radar.match import (
    HoldingRow, KitRow, SetRow, build_taste_makers, build_wanted, match_message,
    matches, normalise, usable,
)

TC1_TEXT = "GMK Testbed Nº3 restock: Light Base, Novelties, now live"
SET_ID = "00000000-0000-4000-8000-000000000001"


def testbed(terms="testbed n3; testbed no3; testbed 3", maker="Fauxmaker"):
    return SetRow(SET_ID, "GMK Testbed Nº3", maker, terms)


def kit(n, name, set_id=SET_ID):
    return KitRow(f"00000000-0000-4000-8000-0000000000{n:02d}", name, [set_id])


class Normalise(unittest.TestCase):
    def test_examples(self):
        self.assertEqual(normalise("GMK Testbed Nº3"), "gmk testbed no3")
        self.assertEqual(normalise("Testbed N°3"), "testbed no3")
        self.assertEqual(normalise("Café-Crème  R2"), "cafe creme r2")
        self.assertEqual(normalise("  a!!b,,c  "), "a b c")


class Matches(unittest.TestCase):
    def test_whole_words_only(self):
        self.assertTrue(matches("testbed no3", "gmk testbed no3 restock"))
        self.assertFalse(matches("test", "gmk testbed"))


class Usable(unittest.TestCase):
    def test_stop_list_and_length(self):
        self.assertFalse(usable("gmk"))
        self.assertFalse(usable("r2"))
        self.assertFalse(usable("ab"))
        self.assertTrue(usable("gmk testbed"))


class BuildWanted(unittest.TestCase):
    def test_terms_are_split_normalised_and_filtered(self):
        w = build_wanted([testbed("testbed n3; testbed no3; testbed 3; kit")], [kit(11, "Light Base")])
        self.assertEqual(w.sets[0].terms, ["testbed n3", "testbed no3", "testbed 3"])

    def test_repeats_dropped(self):
        w = build_wanted([testbed("Testbed N3; testbed n3")], [kit(11, "Light Base")])
        self.assertEqual(w.sets[0].terms, ["testbed n3"])

    def test_blank_terms_use_the_name(self):
        w = build_wanted([testbed("")], [kit(11, "Light Base")])
        self.assertEqual(w.sets[0].terms, ["gmk testbed no3"])

    def test_unusable_name_and_no_terms_gives_no_terms(self):
        w = build_wanted([SetRow(SET_ID, "GMK", "", "kit")], [kit(11, "Light Base")])
        self.assertEqual(w.sets[0].terms, [])
        self.assertEqual(w.sets_without_terms, 1)

    def test_only_sets_with_wanted_kits_and_all_names_mapped(self):
        other = SetRow("00000000-0000-4000-8000-000000000002", "GMK Elsewhere", "", "")
        w = build_wanted([testbed(), other], [kit(12, "Novelties"), kit(11, "Light Base")])
        self.assertEqual([s.name for s in w.sets], ["GMK Testbed Nº3"])
        self.assertEqual([k.name for k in w.sets[0].kits], ["Light Base", "Novelties"])
        self.assertEqual(w.set_ids_by_name, {"GMK Testbed Nº3": SET_ID, "GMK Elsewhere": other.page_id})
        self.assertEqual(w.as_check_map(), {"GMK Testbed Nº3": ["Light Base", "Novelties"]})


class TasteMakers(unittest.TestCase):
    def test_makers_from_holdings_and_sets_usable_only(self):
        makers = build_taste_makers(
            [testbed(maker="Fauxmaker"), SetRow("x", "GMK Other", "GMK", "")],
            [HoldingRow("Fauxmaker"), HoldingRow("Sample Sculpts"), HoldingRow("")],
        )
        self.assertEqual(sorted(makers), ["fauxmaker", "sample sculpts"])


class MatchMessage(unittest.TestCase):
    def test_tc1_single_wanted_kit_matches(self):
        w = build_wanted([testbed()], [kit(11, "Light Base")])
        m = match_message(TC1_TEXT, w, [])
        self.assertEqual(len(m.wanted), 1)
        self.assertEqual([k.name for k in m.wanted[0].kits], ["Light Base"])
        self.assertFalse(m.candidate)

    def test_only_kit_used_when_no_kit_named(self):
        w = build_wanted([testbed()], [kit(11, "Light Base")])
        m = match_message("Testbed N3 back in stock", w, [])
        self.assertEqual([k.name for k in m.wanted[0].kits], ["Light Base"])

    def test_two_wanted_kits_neither_named_is_candidate(self):
        w = build_wanted([testbed()], [kit(11, "Light Base"), kit(12, "Spacebars Plus")])
        m = match_message("testbed no3 extras restock", w, [])
        self.assertEqual(m.wanted, [])
        self.assertTrue(m.candidate)

    def test_taste_maker_alone_is_candidate(self):
        w = build_wanted([testbed()], [kit(11, "Light Base")])
        m = match_message("WTS Fauxmaker cosmo test $60", w, ["fauxmaker"])
        self.assertEqual(m.wanted, [])
        self.assertTrue(m.candidate)

    def test_nothing_wanted(self):
        w = build_wanted([testbed()], [kit(11, "Light Base")])
        m = match_message("Example Vendor restocks switches", w, ["fauxmaker"])
        self.assertEqual(m.wanted, [])
        self.assertFalse(m.candidate)

    def test_several_sets_can_match(self):
        other = SetRow("00000000-0000-4000-8000-000000000002", "GMK Elsewhere", "", "elsewhere")
        w = build_wanted([testbed(), other],
                         [kit(11, "Light Base"), kit(21, "Light Base", other.page_id)])
        m = match_message("testbed n3 and elsewhere restock", w, [])
        self.assertEqual(sorted(x.set.name for x in m.wanted), ["GMK Elsewhere", "GMK Testbed Nº3"])
