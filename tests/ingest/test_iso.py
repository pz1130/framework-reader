import hashlib
from pathlib import Path

from framework_reader.ingest.iso import parse_800_53_to_iso, parse_iso_skeleton
from framework_reader.schema.entities import LicenseTier
from framework_reader.schema.mapping import ProvenanceLevel

# SHA-256 of ISO/IEC 27002:2022 official English control titles (97 rows).
# The titles themselves are not stored — spec §4.1. If a skeleton label
# hashes to one of these, it is the official wording and must not ship.
OFFICIAL_ISO27002_TITLE_SHA256 = frozenset({
    "0063222e4bca8f9f7a90911b2f0c7724313990c17781f2afed1f81f1d81dc4eb",
    "01bb5cb53fde144e8dcc86de72c7c7249f50b3d9de627e5e389e6e9800746a2a",
    "03d3b7595d2877bdb11beddc11c61dfd2515e5556299c0bc234a4b971dc1e14a",
    "07b67508b7e0d279b4d53b5cbcfb3830d706d8c1926b0abec0493b4ecce1a2c4",
    "0bf1d245547b7b2a2c59d3c80a6348ffddb8d38f8e19046aff89ef34fc6fc4e0",
    "0dd0bdac9028d65759711c87e805fcc6da8df5160323818c122fc38064d77b2b",
    "0e019620efef6a19080d5209c45460a0be30480bcef2758b37b7a0586db04ae7",
    "151b7937980a62746476043c034c3128590fdee56e7abda951867441f98d716d",
    "2014ab431982929e2a65b2e6c3a3c5b5bef4236bd2e494b52f4071e200ca9027",
    "26c0d821222ca3e5acc9e51f51fdcf6f89180eb53957a27b042f1cfb183306df",
    "2d6dfcef70c6682ad1bbc4a78cb8198fdadbf73cde8eeb7f3de0e57a39d14f6e",
    "3c337c2b4b2600dbab96c526580cfd193361e7c69f2792ae0d591fed804def5a",
    "3d67a98bf42f36e16695ed1ae5d4148f8bb339f9bff1d8cf64e76b8443be2ea5",
    "3d8d8c0de14aa16ce1a8b1781e3d8eed04fd492be4634625b6bdb0056bd46fd1",
    "3f40acbac24d48cc678fa969a5ef1d1c40fecaf5c4fae8a688d920c32d7dc7e5",
    "40df659af8d7e909d4d498f2a5c77356772239976b0455019a49c9bba48f23fb",
    "45a913f769a0c239d158ae801046b579e93bf6a39114a623554fdf8b34564f40",
    "4980b8c41c1ed960ffb74229aa540e022e3cc248897648ba8a5cbb5bcd04f3ad",
    "4b474deaee5f9c7904b4757c6c782b061d520f1a1560e4301fb941ec6779ec48",
    "4c4f03abfa8d1f77ab584a6eaf3804fee1c9c37f03cd0d1ca691f703f2daa818",
    "4c690f37ff55e97618937a8a3f6e8e511ea3b7307385a3a16aa7d3de9d03faa8",
    "53c7f5d27f41ec175b8c80872db57f11fa8891b3727a7a04fdc13efae44d8831",
    "56d5467cdfdcf8faf0a79073935c88ad21807449c407275de33fb5f253c76d68",
    "57cb7eb80ea6521ab8ff676b6a324ea8f7cdf0fb82448d0c6680e24598984255",
    "58098ee024bb60eba2a9b861051237718d308e70ea6d227a5ceccc6be540393a",
    "595ff57a3af6cc33da2c10c2d9642b0635df2ed0085a6d3d25a5ae4329bde803",
    "5e7dc7507a20053b7a3563996eab88ddee88b59d4d2127050a808ead167a42d6",
    "61303bc749a6597e579c506daf845c46b9f1044914e3aaa0f7cfddb970445cea",
    "644a58082dc24694710acf85180b93fb9cca5bc473c91331937d8ce38d58bb95",
    "65d8feb70a836a6a7f04c0e436a0bae163a1e1576a40ce05475eb3002f68faaf",
    "65f98ae069eebeab164c93a58a333f7ca08dd09cd3960708b73b95b9294d1c89",
    "67302ace25d9decb8558e0703d890ee72c30f8f6e158994bbe703ff7475d9a86",
    "679dc8a5c9b91660fa4361e9ce965570fc667c0d00abff32f6eeeea9b344a931",
    "6a4aaf5aaad0eef7174d37a8e7fe3fb0f31e34cec45f210cc8dfecced612610d",
    "6c833399df7ba6f45effda765cf91432c39204f293bdadedeede0fe0d83a3edc",
    "6fe3f4f7f0aac6f50992c689108d22974973d51129aec60027debfe7ef3d221f",
    "7105003c767d61cd523318547c0db9a0356986eace6fff5773bea43cbdf52000",
    "7149e43b44868852d4310b3a5cb9108c170bf1521f6678c31e43a383a1d4b013",
    "7287c727c6d6f95b91599fd295b8fd8776cc752839ee54e8a07abf1829da6a90",
    "74b7ff569aa9ac63592b9c6657ba3d97b0d2edd2cc78981eca3fcbb21853999c",
    "768f4baecdf069301f5ae55ceaac2413e7989b21901754cc2760c2cfb80f967a",
    "770df7e8a65a209b9b7bd71d1cda3a4ad5e13a88a664134bbeb8f6b7430a051e",
    "7bb7558256cf461a6eb02675d9dee617addcc63e764894b232c82d2cc3c70b57",
    "7c26b2360ec31be4b08dfb36e376a4d485d8a14b8b8774c50b041a34b246fc69",
    "7e4fcc77c7b74f82338e059af6c841c2afa15da14507923b142a5a59640efa25",
    "85dba6e466951c2aed1af0cea58b26ddc8c876b6523007729ec5cc9b5eec8788",
    "870dcaca1ff13162bdf08a4358971cba188cefc82f21b2a18dc51c8881547a54",
    "876ad5816c70dc8685f4913355790e35280f057d8d27e00600ef0b316eb53076",
    "8898141a698ac23a55c730ffa37c0a86242d3a2021c7dc71b95f6689a89d8cf9",
    "8c99c98a0485793aadf486ae3b8ff63000306c8f2a42ec3754fe242cf649f521",
    "8d8ff17370231a91d0a72ece16abc9f01f737b7d334a4c3da2af3607e77e0b97",
    "908283da22483d976956fc0de1e194cb7e569955debef9a89c3002ad2e1611d7",
    "92d03e5919c3957a282b289e43feb0d6586fec714619cac8410d7c809c3de4b1",
    "973aa222586675fc7fbe6bdea660bea266c21f2c1296e9e50a157dfa097e7beb",
    "a07c592857f9295a478beea4a1cd4cdf8f3399b757234c77133ef9e16743a844",
    "a1042b922079877edfb2988fda93f301ceb9dbadeccc7cfbf64aa014842f9011",
    "a49de7a974abc1972ec4040af47be911dc9c0681c403bbd21d4bc9361e39cd3c",
    "a4c2f356432ea8f441fea2eab6bf341ec2368b8e0fbc565f33fa54b664d077f4",
    "a6294f33e36fb3ab518769b1e8208a2ac4b3c85ea25350c6f5feb182c2535615",
    "a714b3c44405fafb9616a6ff813914c415135273034b153d3d8d84f1dcb30c10",
    "ab4e313f4df20482108fee3650b8ead0f8dca06c4323ee56bf50be86ce145a44",
    "ab59d0ab82e04651f9e6537b1e5460482bf6dea24e4f66d2965a55d2a2576f2c",
    "ab6bb2c1a0ee98fbcfaf6adbb026d3ca7d82fc270fbfd10030de2a1f985fadd4",
    "b010eb250a1d27a905b314edbea2a05aa3457fea35f6fe4e239f366b2acc9a8b",
    "b36f27d45c91b108b181005c11b504e025f8f99256b65ab05ffe293ca33e0f63",
    "bbc9545efe486740c96e140229533fabd2a7e04143228f0c970ec6014fa0753a",
    "c0c26c613bd0b5326d27b4d097f73eca3134476cc867fe21304c46c00e47ef46",
    "c0eec47a5012bc910bc089d5858a945c8eed4beeb8b9f0cb46a8e5c407c837dc",
    "c27332c40df42aef0e1822122328df277d6e8dd67a817e480ed392b70f047cb1",
    "c9b526e9e3bb3799e6bbd4ddc776da887d269eeef9dc4210188e2fb1e6ce34a1",
    "cb69bd9d9a4eea7882b1afa413f4fdd65c25c8113806111ecabf84463a89c416",
    "ce6d25a746cd3e28b9d5f08ff51ca4755d0576a7efcd43e3bbec972f9e7b8d08",
    "cffd743cb7c3a0cc89a88999a284be0cb0b8c8a82b5de74b143626c9dbb275ef",
    "d3ef01b4a9c9910364c9b26b2499c8787a0461d2d24ab80376fff736a288b34c",
    "d5dfb07efdac556e870fb24a2611f49f3c59112cc600a397059c9ad8a068946c",
    "d5e7a955e60f6007e8d963c5899374a4fe5dba7d8fe7014e7f624652977c908c",
    "d772f809f9c7ce07f400d517f69d24ccda8b2655cfffd15a457675f3ac674a83",
    "d9edaf97d5cf4d27557ceae9542780561717725414a5b3541309d134e67cc481",
    "da060944dc4756fec12b5c238b90bf0b5482a41b051b1e2e31181d5476b6382d",
    "da71c82d6394e7ebebec1f853573ce7cf1e8ec99f4bbb7bb53a8bc107bd3a8bc",
    "db500583e1bd988c928e34a1019417681f2d7f84a31b98cf605f3fb0e99ab4d8",
    "dc7fd8de8ce0e203d757f7cbe244a598b83102d203888339a377f7830c416ca8",
    "e31f1c82e1e0d6cf716f76d16691643c72ff66ecb9573264d40f49f69a00d1ac",
    "e40319c4b5b4770dbafc85835fdbb95869cad1cf30ed869bce86a28eec3f6fd8",
    "e83228780a342a528bfcebb03164a6f231e9e9132ab5960c0cca5f25c71ea603",
    "e8c419c0ff98bcde58d21e99fabf41df0eda46d172492113de2a180d29c5a6d1",
    "ec76423e0c9528d1963b964f15616241d801ba145b2c6560c8a64cb51e41ccb2",
    "ec96af342ec4ced466b0d5d6ef95ff49c00e5b01772b8141b72359c9f38de4c5",
    "ecd7a498c14bc619b282592aef0fe646066e844025823c1eb44ef84c503fd471",
    "f11e55c72ebcb0d0340ef7a218104f79c01cb43266db57e3c9a83893680eeb0f",
    "f164a30f93aa174a6965e9e20e9e7a03eea7a580e76e1c20f84e74541cb68722",
    "f377cd0ae858a7fabac19d115747ed9565e9cc4eb2817c917fbfac9f30dad30b",
    "f50743fd686867878d51bb09a58aca8d08ac6b90321714556c8a86e8edd971be",
    "f6f26fc1e57a3c81ea083388223bfd0e45862fab3ad7dcc8fdcdc7a46baeb00d",
    "fa5f29fd3ce0b2f9b5bcd14583805bb7b6e60762859b756a1709a5339319c173",
    "faa4eac27b52a54f4078231c2d3a545bb7002c31fb8b5384948cfa8ead5573f3",
    "fc96b1d42ce8b6310078854e14b25d109b218155ed29f1e3b5c3e197fc20d62e",
})


SKELETON = Path("content/iso27002_2022_skeleton.csv")
ISO_MAP_FIXTURE = Path("tests/fixtures/sp800-53_to_iso_sample.xlsx")


def test_iso_framework_is_tier_c():
    fw, _ = parse_iso_skeleton(SKELETON)
    assert fw.tier is LicenseTier.C_PURCHASE


def test_no_iso_control_claims_original_label():
    """Tier C 不得使用官方标题原文。spec §4.1"""
    _, controls = parse_iso_skeleton(SKELETON)
    assert controls
    assert all(c.label_is_original is False for c in controls)
    assert all(c.label.strip() for c in controls), "每条都必须有自写 label"


def test_iso_skeleton_labels_are_not_official_titles():
    """标志位是写死的，拦不住有人把官方英文标题贴进 CSV。用标题哈希钉住。"""
    assert len(OFFICIAL_ISO27002_TITLE_SHA256) == 97
    _, controls = parse_iso_skeleton(SKELETON)
    hits = [
        c.id
        for c in controls
        if hashlib.sha256(c.label.encode("utf-8")).hexdigest()
        in OFFICIAL_ISO27002_TITLE_SHA256
    ]
    assert hits == [], f"这些 ISO label 是官方标题原文：{hits}"


def test_control_ids_match_iso_numbering():
    _, controls = parse_iso_skeleton(SKELETON)
    ids = [c.id for c in controls]
    assert len(ids) == len(set(ids))
    assert all(cid.startswith("ISO-27002-2022:A.") for cid in ids)


def test_skeleton_csv_has_no_original_text_column():
    """CSV 结构本身就不给原文留位置。"""
    header = SKELETON.read_text(encoding="utf-8").splitlines()[0]
    cols = {c.strip() for c in header.split(",")}
    assert cols == {"control_id", "label_zh", "parent_id"}


def test_skeleton_covers_all_93_controls():
    _, controls = parse_iso_skeleton(SKELETON)
    leaves = [c for c in controls if c.parent_id is not None]
    assert len(leaves) == 93, f"ISO 27002:2022 有 93 条控制，当前 {len(leaves)}"
    expected = {
        f"ISO-27002-2022:A.{theme}.{n}"
        for theme, last in ((5, 37), (6, 8), (7, 14), (8, 34))
        for n in range(1, last + 1)
    }
    assert {c.id for c in leaves} == expected


def test_parse_800_53_to_iso_yields_namespaced_l1_edges():
    edges = parse_800_53_to_iso(ISO_MAP_FIXTURE)
    assert edges, "夹具应至少解析出一条边"
    assert all(e.provenance.level is ProvenanceLevel.L1_OFFICIAL for e in edges)
    assert all(e.provenance.source == "NIST-SP800-53r5-to-iso-27001" for e in edges)
    assert all(e.from_id.startswith("NIST-800-53-R5:") for e in edges)
    assert all(e.to_id.startswith("ISO-27002-2022:A.") for e in edges)
    pairs = {(e.from_id, e.to_id) for e in edges}
    assert (
        "NIST-800-53-R5:AC-01",
        "ISO-27002-2022:A.5.1",
    ) in pairs
    assert (
        "NIST-800-53-R5:AC-03",
        "ISO-27002-2022:A.8.16",
    ) in pairs, "裸编号 8.16 应归一到骨架 A.8.16"
    assert all("7.5.1" not in e.to_id for e in edges)
    assert (
        "NIST-800-53-R5:AT-03",
        "ISO-27002-2022:A.5.2",
    ) not in pairs, "27001 条款 5.2 不得误接到 A.5.2"
    assert all(e.from_id.strip() and e.to_id.strip() for e in edges)
