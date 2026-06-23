"""Юнит-тесты ядра (без GUI, без сканеров, без Windows). Запуск:

    python3 -m unittest tests.test_core -v
"""
import unittest

from armcore import transliteration as tr
from armcore import mrz
from armcore import services, storage


class TestTransliteration(unittest.TestCase):
    def test_basic_words(self):
        self.assertEqual(tr.translit_lat_to_cyr("ivan"), "иван")
        self.assertEqual(tr.translit_lat_to_cyr("petrov"), "петров")

    def test_digraphs(self):
        self.assertEqual(tr.translit_lat_to_cyr("shashlik"), "шашлик")
        self.assertEqual(tr.translit_lat_to_cyr("zhuk"), "жук")
        self.assertEqual(tr.translit_lat_to_cyr("chay"), "чаы")  # y неоднозначна

    def test_case_preserved(self):
        self.assertEqual(tr.translit_lat_to_cyr("IVAN"), "ИВАН")
        self.assertEqual(tr.translit_lat_to_cyr("Ivan"), "Иван")

    def test_passport_digraphs(self):
        # паспортные формы kh, shch, ts
        self.assertEqual(tr.translit_lat_to_cyr("akhmedov"), "ахмедов")
        self.assertEqual(tr.translit_lat_to_cyr("shcherba"), "щерба")

    def test_title_name(self):
        self.assertEqual(tr.transliterate_name("IVANOV"), "Иванов")
        self.assertEqual(tr.transliterate_name("ABDU-RAZAK"), "Абду-Разак")

    def test_unknown_chars_passthrough(self):
        self.assertEqual(tr.translit_lat_to_cyr("ivan 1990"), "иван 1990")


class TestMrz(unittest.TestCase):
    def test_check_digit(self):
        # Пример из ICAO Doc 9303.
        self.assertEqual(mrz.check_digit("520727"), 3)
        self.assertEqual(mrz.check_digit("AB2134"), 5)

    def test_parse_td3_specimen(self):
        # Классический пример паспорта (ICAO specimen).
        line1 = "P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<"
        line2 = "L898902C36UTO7408122F1204159ZE184226B<<<<<10"
        res = mrz.parse_td3(line1, line2)
        self.assertEqual(res.document_type, "P")
        self.assertEqual(res.country_code, "UTO")
        self.assertEqual(res.family_latin, "ERIKSSON")
        self.assertEqual(res.given_latin, "ANNA MARIA")
        self.assertEqual(res.passport_number, "L898902C3")
        self.assertEqual(res.nationality, "UTO")
        self.assertEqual(res.sex, "Ж")
        self.assertEqual(res.birth_date, "12.08.1974")
        self.assertEqual(res.expiry_date, "15.04.2012")

    def test_parse_from_text(self):
        text = """
        P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<
        L898902C36UTO7408122F1204159ZE184226B<<<<<10
        """
        res = mrz.parse(text)
        self.assertIsNotNone(res)
        self.assertEqual(res.passport_number, "L898902C3")

    def test_to_fields(self):
        line1 = "P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<"
        line2 = "L898902C36UTO7408122F1204159ZE184226B<<<<<10"
        f = mrz.parse_td3(line1, line2).to_fields()
        self.assertEqual(f["FAMILY"], "Ерикссон")  # начальную Э правят вручную
        self.assertEqual(f["NAME"], "Анна")
        self.assertEqual(f["PATRONYMIC"], "Мариа")
        self.assertEqual(f["SEX"], "Ж")


class TestServices(unittest.TestCase):
    def test_service_list_nonempty(self):
        self.assertTrue(len(services.SERVICES) >= 18)

    def test_contract_implies_act(self):
        # Договор на обучение/сопровождение -> авто-формирование акта.
        docs = services.documents_for_selection(["Договор на обучение"])
        self.assertIn("Договор на обучение", docs)
        self.assertIn("Акт", docs)

    def test_is_study_client(self):
        self.assertTrue(services.is_study_client(["Договор на обучение"]))
        self.assertFalse(services.is_study_client(["Скан паспорта"]))


class TestStorage(unittest.TestCase):
    def test_client_folder_name(self):
        name = storage.client_folder_name("Иванов", "Иван", "Иванович", date_str="01062026")
        self.assertEqual(name, "Иванов_Иван_Иванович_01062026")

    def test_client_folder_name_from_fio(self):
        name = storage.client_folder_name_from_fio("Иванов Иван Иванович", date_str="01062026")
        self.assertEqual(name, "Иванов_Иван_Иванович_01062026")


if __name__ == "__main__":
    unittest.main(verbosity=2)
