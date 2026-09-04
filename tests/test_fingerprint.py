"""Error fingerprint tests.

The property that matters is separation: two different faults must not collapse
into overlapping identity just because both are "an error on startup".
"""

from repo_troubleshooter.fingerprint.error import fingerprint, normalize, tokenize

LOADER = (
    "On Windows, dsh web starts but __DSH_BOOT__ has zero entries; client-modules reports "
    "HTML did not preload @deepseek-ai/dsh-client-modules/client.js; "
    "TypeError: e.indexOf is not a function"
)
POSTGRES = (
    "PostgreSQL startup failed: connection refused at 127.0.0.1:5432 while applying migrations"
)


class TestNormalization:
    def test_strips_absolute_paths_line_numbers_and_ids(self):
        raw = (
            "Error: Service `llm` not found\n"
            "    at Registry.get (C:\\Users\\abc\\project\\foo.ts:137:11)\n"
            "    request 3f2b7c18-9d0e-4a1b-8c2d-5e6f70819a2b at 2026-08-30T06:03:31Z "
            "on 127.0.0.1:5432 addr 0x7ffd12ab"
        )
        signature = normalize(raw)
        assert "C:\\Users\\abc" not in signature
        assert "137:11" not in signature
        assert "3f2b7c18" not in signature
        assert "0x7ffd12ab" not in signature
        assert "5432" not in signature
        # what identifies the fault survives
        assert "Service `llm` not found" in signature
        assert "Registry.get" in signature
        assert "foo.ts" in signature

    def test_same_fault_on_two_machines_has_the_same_signature(self):
        a = fingerprint("Service `llm` not found at C:\\Users\\ann\\app\\x.ts:10:2")
        b = fingerprint("Service `llm` not found at /home/bob/app/x.ts:998:41")
        assert a.signature_hash == b.signature_hash


class TestDiscrimination:
    def test_keeps_symbols_packages_and_exception_types(self):
        fp = fingerprint(LOADER)
        assert "typeerror" in fp.discriminative
        assert "__dsh_boot__" in fp.discriminative
        assert "@deepseek-ai/dsh-client-modules/client.js" in fp.discriminative
        assert "e.indexof" in fp.discriminative

    def test_two_unrelated_faults_share_no_identifying_token(self):
        loader = set(fingerprint(LOADER).discriminative)
        postgres = set(fingerprint(POSTGRES).discriminative)
        shared = loader & postgres
        assert not shared, f"unrelated errors share identity tokens: {shared}"

    def test_generic_words_are_not_discriminative(self):
        fp = fingerprint("startup failed due to a config error on windows with a plugin enabled")
        for generic in ("startup", "config", "error", "windows", "plugin"):
            assert generic not in fp.discriminative

    def test_error_codes_are_preserved(self):
        fp = fingerprint("Error: connect ECONNREFUSED; code ERR_INVALID_ARG_TYPE")
        assert "econnrefused" in fp.discriminative
        assert "err_invalid_arg_type" in fp.discriminative

    def test_http_route_is_structural_identity(self):
        fp = fingerprint("GET /metrics returns 404 after the upgrade")
        assert "/metrics" in fp.discriminative


class TestTokenize:
    def test_dotted_calls_contribute_their_parts(self):
        tokens = tokenize("TypeError: e.indexOf is not a function")
        assert "e.indexof" in tokens
        assert "indexof" in tokens

    def test_empty_input(self):
        fp = fingerprint(None)
        assert fp.is_empty
        assert fp.discriminative == ()
