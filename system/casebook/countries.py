"""Country normalisation: names, ISO codes and nationalities map to one canonical name.

Territories are their own country (policy 6), so they have their own rows.
"""
import re
import unicodedata

# canonical name | ISO alpha-2 | ISO alpha-3 | other names ; nationalities (comma separated)
_ROWS = """
Afghanistan|AF|AFG|Afghan
Albania|AL|ALB|Albanian
Algeria|DZ|DZA|Algerian
Andorra|AD|AND|Andorran
Angola|AO|AGO|Angolan
Antigua and Barbuda|AG|ATG|Antiguan
Argentina|AR|ARG|Argentine,Argentinian,Argentinean
Armenia|AM|ARM|Armenian
Australia|AU|AUS|Australian
Austria|AT|AUT|Austrian
Azerbaijan|AZ|AZE|Azerbaijani,Azeri
Bahamas|BS|BHS|The Bahamas,Bahamian
Bahrain|BH|BHR|Bahraini
Bangladesh|BD|BGD|Bangladeshi
Barbados|BB|BRB|Barbadian
Belarus|BY|BLR|Belarusian,Belarussian
Belgium|BE|BEL|Belgian
Belize|BZ|BLZ|Belizean
Benin|BJ|BEN|Beninese
Bhutan|BT|BTN|Bhutanese
Bolivia|BO|BOL|Bolivian,Plurinational State of Bolivia
Bosnia and Herzegovina|BA|BIH|Bosnia,Bosnian,Bosnia-Herzegovina,Bosnia & Herzegovina
Botswana|BW|BWA|Botswanan,Motswana
Brazil|BR|BRA|Brazilian,Brasil
Brunei|BN|BRN|Bruneian,Brunei Darussalam
Bulgaria|BG|BGR|Bulgarian
Burkina Faso|BF|BFA|Burkinabe
Burundi|BI|BDI|Burundian
Cambodia|KH|KHM|Cambodian
Cameroon|CM|CMR|Cameroonian
Canada|CA|CAN|Canadian
Cape Verde|CV|CPV|Cabo Verde,Cape Verdean
Central African Republic|CF|CAF|Central African
Chad|TD|TCD|Chadian
Chile|CL|CHL|Chilean
China|CN|CHN|Chinese,People's Republic of China,PRC
Colombia|CO|COL|Colombian
Comoros|KM|COM|Comorian
Congo|CG|COG|Republic of the Congo,Congo-Brazzaville,Congolese
Democratic Republic of the Congo|CD|COD|DR Congo,DRC,Congo-Kinshasa,Democratic Republic of Congo
Costa Rica|CR|CRI|Costa Rican
Côte d'Ivoire|CI|CIV|Cote d'Ivoire,Cote D'Ivoire,Ivory Coast,Ivorian
Croatia|HR|HRV|Croatian
Cuba|CU|CUB|Cuban
Cyprus|CY|CYP|Cypriot
Czech Republic|CZ|CZE|Czechia,Czech
Denmark|DK|DNK|Danish,Dane
Djibouti|DJ|DJI|Djiboutian
Dominica|DM|DMA|
Dominican Republic|DO|DOM|Dominican
Ecuador|EC|ECU|Ecuadorian,Ecuadorean
Egypt|EG|EGY|Egyptian
El Salvador|SV|SLV|Salvadoran,Salvadorean
Equatorial Guinea|GQ|GNQ|Equatoguinean
Eritrea|ER|ERI|Eritrean
Estonia|EE|EST|Estonian
Eswatini|SZ|SWZ|Swaziland,Swazi
Ethiopia|ET|ETH|Ethiopian
Fiji|FJ|FJI|Fijian
Finland|FI|FIN|Finnish,Finn
France|FR|FRA|French
Gabon|GA|GAB|Gabonese
Gambia|GM|GMB|The Gambia,Gambian
Georgia|GE|GEO|Georgian
Germany|DE|DEU|German,Deutschland
Ghana|GH|GHA|Ghanaian
Greece|GR|GRC|Greek
Grenada|GD|GRD|Grenadian
Guatemala|GT|GTM|Guatemalan
Guinea|GN|GIN|Guinean
Guinea-Bissau|GW|GNB|Guinea Bissau
Guyana|GY|GUY|Guyanese
Haiti|HT|HTI|Haitian
Honduras|HN|HND|Honduran
Hungary|HU|HUN|Hungarian
Iceland|IS|ISL|Icelandic,Icelander
India|IN|IND|Indian
Indonesia|ID|IDN|Indonesian
Iran|IR|IRN|Iranian,Islamic Republic of Iran
Iraq|IQ|IRQ|Iraqi
Ireland|IE|IRL|Irish,Republic of Ireland
Israel|IL|ISR|Israeli
Italy|IT|ITA|Italian,Italia
Jamaica|JM|JAM|Jamaican
Japan|JP|JPN|Japanese
Jordan|JO|JOR|Jordanian
Kazakhstan|KZ|KAZ|Kazakh,Kazakhstani
Kenya|KE|KEN|Kenyan
Kosovo|XK|XKX|Kosovar,Kosovan
Kuwait|KW|KWT|Kuwaiti
Kyrgyzstan|KG|KGZ|Kyrgyz,Kyrgyz Republic
Laos|LA|LAO|Lao,Laotian,Lao PDR,Lao People's Democratic Republic
Latvia|LV|LVA|Latvian
Lebanon|LB|LBN|Lebanese
Lesotho|LS|LSO|Basotho
Liberia|LR|LBR|Liberian
Libya|LY|LBY|Libyan
Liechtenstein|LI|LIE|Liechtensteiner
Lithuania|LT|LTU|Lithuanian
Luxembourg|LU|LUX|Luxembourgish,Luxembourger
Madagascar|MG|MDG|Malagasy
Malawi|MW|MWI|Malawian
Malaysia|MY|MYS|Malaysian
Maldives|MV|MDV|Maldivian
Mali|ML|MLI|Malian
Malta|MT|MLT|Maltese
Mauritania|MR|MRT|Mauritanian
Mauritius|MU|MUS|Mauritian
Mexico|MX|MEX|Mexican,México
Moldova|MD|MDA|Moldovan,Republic of Moldova
Monaco|MC|MCO|Monegasque
Mongolia|MN|MNG|Mongolian
Montenegro|ME|MNE|Montenegrin
Morocco|MA|MAR|Moroccan
Mozambique|MZ|MOZ|Mozambican
Myanmar|MM|MMR|Burma,Burmese
Namibia|NA|NAM|Namibian
Nepal|NP|NPL|Nepali,Nepalese
Netherlands|NL|NLD|The Netherlands,Dutch,Holland
New Zealand|NZ|NZL|New Zealander
Nicaragua|NI|NIC|Nicaraguan
Niger|NE|NER|Nigerien
Nigeria|NG|NGA|Nigerian
North Korea|KP|PRK|North Korean,DPRK,Democratic People's Republic of Korea
North Macedonia|MK|MKD|Macedonia,Macedonian
Norway|NO|NOR|Norwegian
Oman|OM|OMN|Omani
Pakistan|PK|PAK|Pakistani
Palestine|PS|PSE|Palestina,Palestinian,Palestinian Territories,State of Palestine
Panama|PA|PAN|Panamanian
Papua New Guinea|PG|PNG|Papua New Guinean
Paraguay|PY|PRY|Paraguayan
Peru|PE|PER|Peruvian
Philippines|PH|PHL|The Philippines,Filipino,Philippine
Poland|PL|POL|Polish
Portugal|PT|PRT|Portuguese
Qatar|QA|QAT|Qatari
Romania|RO|ROU|Romanian
Russia|RU|RUS|Russian,Russian Federation
Rwanda|RW|RWA|Rwandan
Saint Kitts and Nevis|KN|KNA|St Kitts and Nevis
Saint Lucia|LC|LCA|St Lucia
Saint Vincent and the Grenadines|VC|VCT|St Vincent and the Grenadines
Samoa|WS|WSM|Samoan
San Marino|SM|SMR|
Saudi Arabia|SA|SAU|Saudi,Saudi Arabian
Senegal|SN|SEN|Senegalese
Serbia|RS|SRB|Serbian
Seychelles|SC|SYC|Seychellois
Sierra Leone|SL|SLE|Sierra Leonean
Singapore|SG|SGP|Singaporean
Slovakia|SK|SVK|Slovak,Slovakian,Slovak Republic
Slovenia|SI|SVN|Slovenian,Slovene
Somalia|SO|SOM|Somali,Somalian
South Africa|ZA|ZAF|South African
South Korea|KR|KOR|South Korean,Republic of Korea,Korea
South Sudan|SS|SSD|South Sudanese
Spain|ES|ESP|Spanish,España
Sri Lanka|LK|LKA|Sri Lankan
Sudan|SD|SDN|Sudanese
Suriname|SR|SUR|Surinamese
Sweden|SE|SWE|Swedish,Swede
Switzerland|CH|CHE|Swiss
Syria|SY|SYR|Syrian,Syrian Arab Republic
Taiwan|TW|TWN|Taiwanese
Tajikistan|TJ|TJK|Tajik
Tanzania|TZ|TZA|Tanzanian,United Republic of Tanzania
Thailand|TH|THA|Thai
Togo|TG|TGO|Togolese
Tonga|TO|TON|Tongan
Trinidad and Tobago|TT|TTO|Trinidadian,Trinidad
Tunisia|TN|TUN|Tunisian
Turkey|TR|TUR|Turkish,Türkiye,Turkiye
Turkmenistan|TM|TKM|Turkmen
Uganda|UG|UGA|Ugandan
Ukraine|UA|UKR|Ukrainian
United Arab Emirates|AE|ARE|UAE,U.A.E.,Emirates,Emirati
United Kingdom|GB|GBR|UK,U.K.,Great Britain,Britain,British,England,Scotland,Wales,Northern Ireland,English,Scottish,Welsh
United States|US|USA|United States of America,U.S.,U.S.A.,America,American
Uruguay|UY|URY|Uruguayan
Uzbekistan|UZ|UZB|Uzbek
Vanuatu|VU|VUT|
Vatican City|VA|VAT|Holy See,Vatican
Venezuela|VE|VEN|Venezuelan,Bolivarian Republic of Venezuela
Vietnam|VN|VNM|Viet Nam,Vietnamese
Yemen|YE|YEM|Yemeni
Zambia|ZM|ZMB|Zambian
Zimbabwe|ZW|ZWE|Zimbabwean
Hong Kong|HK|HKG|Hong Kong SAR,Hong Kong Special Administrative Region of China
Macao|MO|MAC|Macau,Macao Special Administrative Region of China,Macau SAR
Puerto Rico|PR|PRI|Puerto Rican
Northern Mariana Islands|MP|MNP|
Guam|GU|GUM|
American Samoa|AS|ASM|
United States Virgin Islands|VI|VIR|US Virgin Islands,U.S. Virgin Islands
British Virgin Islands|VG|VGB|BVI
Cayman Islands|KY|CYM|
Bermuda|BM|BMU|
Gibraltar|GI|GIB|
Jersey|JE|JEY|
Guernsey|GG|GGY|
Isle of Man|IM|IMN|
Anguilla|AI|AIA|
Turks and Caicos Islands|TC|TCA|
Montserrat|MS|MSR|
Aruba|AW|ABW|
Curaçao|CW|CUW|Curacao
Sint Maarten|SX|SXM|
Greenland|GL|GRL|
Faroe Islands|FO|FRO|
French Polynesia|PF|PYF|
New Caledonia|NC|NCL|
Guadeloupe|GP|GLP|
Martinique|MQ|MTQ|
Réunion|RE|REU|Reunion
French Guiana|GF|GUF|
Cook Islands|CK|COK|
Marshall Islands|MH|MHL|
Micronesia|FM|FSM|Federated States of Micronesia
Palau|PW|PLW|
Solomon Islands|SB|SLB|
Timor-Leste|TL|TLS|East Timor
Kiribati|KI|KIR|
Nauru|NR|NRU|Nauruan
Tuvalu|TV|TUV|Tuvaluan
São Tomé and Príncipe|ST|STP|Sao Tome and Principe,Sao Tome
Western Sahara|EH|ESH|
Falkland Islands|FK|FLK|Falklands
Saint Helena|SH|SHN|St Helena
Niue|NU|NIU|
Tokelau|TK|TKL|
Wallis and Futuna|WF|WLF|
Saint Pierre and Miquelon|PM|SPM|St Pierre and Miquelon
Mayotte|YT|MYT|
Saint Martin|MF|MAF|St Martin
Saint Barthélemy|BL|BLM|Saint Barthelemy,St Barthelemy
Bonaire|BQ|BES|Caribbean Netherlands
Åland Islands|AX|ALA|Aland Islands,Aland
Norfolk Island|NF|NFK|
Christmas Island|CX|CXR|
Cocos Islands|CC|CCK|Cocos (Keeling) Islands
Pitcairn Islands|PN|PCN|Pitcairn
"""

NOT_COUNTRIES = {"international", "europe", "unknown", "n/a", "na", "none", "worldwide", "global"}


def _key(s):
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.replace("’", "'").lower()
    s = re.sub(r"[.’]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    if s.startswith("the "):
        s = s[4:]
    return s


_BY_KEY = {}
_CODES = {}
_TEXT_TERMS = []   # (term as written, canonical): read in any running text
_LOOSE_TERMS = []  # the same plus words that are often part of another name: customer notes only

for _line in _ROWS.strip().splitlines():
    _name, _a2, _a3, _alts = _line.split("|")
    _alt_list = [a.strip() for a in _alts.split(",") if a.strip()]
    for _t in [_name] + _alt_list:
        _BY_KEY[_key(_t)] = _name
        # "English" and the like read as languages or jobs in running text.
        if (len(_t) > 3 or _t in ("UK", "UAE", "DRC", "BVI")) and _t not in ("English", "Welsh", "Scottish", "Dane", "Finn", "Swede"):
            _LOOSE_TERMS.append((_t, _name))
            # Words that are often part of another name ("Bank of America", the airline).
            if _t not in ("Korea", "America", "Britain", "Holland", "Emirates"):
                _TEXT_TERMS.append((_t, _name))
    _CODES[_a2] = _name
    _CODES[_a3] = _name

# Longest terms first so "South Africa" wins over "Africa"-like fragments
# and "Northern Mariana Islands" over "Mariana".
def _text_re(terms):
    terms.sort(key=lambda t: -len(t[0]))
    return re.compile(
        r"(?<![A-Za-z])(" + "|".join(re.escape(t).replace(r"\ ", r"\s+").replace("'", "['’]") for t, _ in terms) + r")(?![A-Za-z])"
    )


_TEXT_RE = _text_re(_TEXT_TERMS)
_LOOSE_RE = _text_re(_LOOSE_TERMS)
_TEXT_LOOKUP = {_key(t): c for t, c in _LOOSE_TERMS}


def country(value):
    """Canonical country for a field value (name, code or nationality). None if not a country."""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    if _key(s) in NOT_COUNTRIES:
        return None
    if s.isupper() and len(s) in (2, 3) and s in _CODES:
        return _CODES[s]
    k = _key(s)
    if k in _BY_KEY:
        return _BY_KEY[k]
    return None


def is_named_place(value):
    """True for a value that names somewhere but is not a recognised country,
    so the country behind it stays unknown rather than absent."""
    if value is None:
        return False
    s = re.sub(r"\s*\[[^\]]*\]\s*$", "", str(value).strip())
    last = s.split(",")[-1].strip()
    if not last or _key(last) in NOT_COUNTRIES:
        return False
    return country(last) is None


def country_of_place(value):
    """Country of a place written as 'City, Region, Country [Tag]': its last part."""
    if not value:
        return None
    s = re.sub(r"\s*\[[^\]]*\]\s*$", "", str(value).strip())
    last = s.split(",")[-1].strip()
    return country(last)


def countries_in_text(text, loose=False):
    """Countries named in running text (names and nationalities, capitalised as written).

    Returns canonical names in order of appearance. Codes are not read from text,
    and an all-caps word is not read as a country, because notes write the
    customer's name in capitals.

    `loose` also reads "Britain", "America", "Holland", "Korea" and "Emirates". It is
    for the customer's notes, where missing a place someone lived could clear a true
    match. It is not for search results, where an extra country could make a
    namesake's result look like the hit's.
    """
    out = []
    for m in (_LOOSE_RE if loose else _TEXT_RE).finditer(text or ""):
        c = _TEXT_LOOKUP.get(_key(m.group(1)))
        if c and c not in out:
            out.append(c)
    return out
