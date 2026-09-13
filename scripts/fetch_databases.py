"""Fetch the three demo databases into registry/data/, which the repository does not carry.

Run once after cloning, before the app and before the tests:

    uv run python scripts/fetch_databases.py

Idempotent, and no new dependency: the standard library alone. Every file is verified against the
SHA-256 pinned below before it is installed, and a download that does not match is reported and
thrown away rather than written — a database is what every answer in this system is derived from,
so a silent substitution would be the worst kind of wrong.

The two GitHub sources are pinned to a commit and a tag rather than to a branch. That is not
cosmetic: `main` of the northwind repository now ships a different schema (`Categories`, `Orders`,
`Order Details` where the Schema Document and every recorded query expect `Category`, `Order`,
`OrderDetail`), and only `v0.1.0` matches.
"""

import hashlib
import io
import sys
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "registry" / "data"
CHUNK = 1 << 20


@dataclass(frozen=True)
class Source:
    name: str
    filename: str
    url: str
    sha256: str      # of the file as installed, which for a zip is the member and not the archive
    member: str = ""  # the file to take out of the archive, where the source is one


SOURCES = (
    Source("sakila", "sakila.db",
           "https://github.com/bradleygrant/sakila-sqlite3/raw/7e8438a09184abcac2f0b3740e9bcc93f04ef0fd/sakila_master.db",
           "88c91a4a1a6b61f9d3f35904c0a173c887b25e73f20c3c2fdb073818c06f4268"),
    # The only one of the three not pinnable to a commit: a tutorial site, serving this URL since 2018.
    Source("chinook", "chinook.db",
           "https://www.sqlitetutorial.net/wp-content/uploads/2018/03/chinook.zip",
           "d6c11e2ccda2a36883af57f031f76fbd55e247f3be1710c4c8aaa72ec06a18cb", member="chinook.db"),
    Source("northwind_small", "northwind_small.sqlite",
           "https://github.com/jpwhite3/northwind-SQLite3/raw/v0.1.0/Northwind_small.sqlite",
           "4a13fa29a14dc296e6306f490d6b75f898efaa727038a48d5ae3419f1ac3acfd"),
)


def digest(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as f:
        while block := f.read(CHUNK):
            sha.update(block)
    return sha.hexdigest()


# The tutorial site answers urllib's default User-Agent with 403, so every request carries one.
AGENT = {"User-Agent": "Mozilla/5.0 (compatible; data-agents-fetch/1.0)"}


def download(source: Source) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(source.url, headers=AGENT), timeout=120) as response:
        payload = response.read()
    if not source.member:
        return payload
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        return archive.read(source.member)


def install(source: Source) -> bool:
    target = DATA / source.filename
    if target.exists():
        found = digest(target)
        if found == source.sha256:
            print(f"  {source.name:<16} already there, digest matches")
            return True
        print(f"  {source.name:<16} already there but its digest is {found[:12]}…, not the pinned "
              f"{source.sha256[:12]}…; left as it is. Delete {target} and run again to replace it.")
        return True  # somebody else's file, not this script's to overwrite
    try:
        payload = download(source)
    except Exception as e:
        print(f"  {source.name:<16} DOWNLOAD FAILED: {type(e).__name__}: {e}\n{' ' * 18}{source.url}")
        return False
    found = hashlib.sha256(payload).hexdigest()
    if found != source.sha256:
        print(f"  {source.name:<16} DIGEST MISMATCH: got {found}, expected {source.sha256}. "
              f"Nothing was written.\n{' ' * 18}{source.url}")
        return False
    partial = target.with_suffix(target.suffix + ".part")  # a killed run must not leave a half file that looks whole
    partial.write_bytes(payload)
    partial.rename(target)
    print(f"  {source.name:<16} written to {target.relative_to(Path.cwd()) if target.is_relative_to(Path.cwd()) else target}"
          f" ({len(payload) / 1e6:.1f} MB)")
    return True


def main() -> int:
    DATA.mkdir(parents=True, exist_ok=True)
    print(f"Databases into {DATA}")
    ok = [install(source) for source in SOURCES]
    if all(ok):
        print("All three databases are in place.")
        return 0
    print(f"\n{ok.count(False)} of {len(ok)} could not be installed; the system will not run until they are.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
