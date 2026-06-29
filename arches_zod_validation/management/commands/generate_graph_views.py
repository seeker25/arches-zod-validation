"""Management command that generates read-only, user-scoped DRF views for
every resource graph (arches-querysets generic view pattern), a project-level
URLs module for them, and -- if missing -- a default openapi-ts config.

This is step 1 of the API codegen pipeline:

    1. python3.11 manage.py generate_graph_views
    2. python3.11 manage.py spectacular --urlconf bcrhp.urls_api_documented --file schema.yml
    3. npm run openapi:zod

Each step MUST run in its own process: Django's startup system checks import
the URLconf, so a process that ran step 1 may hold stale generated modules in
sys.modules -- chaining spectacular in-process would document the previous
generation. Orchestrate with make / a shell script, not call_command().

The command lives in the ``arches_zod_validation`` app, but the generated
code is written into the Django *project* package (e.g. ``bcrhp``):

    <repo root>/
        openapi-ts.config.ts           <- scaffolded only if missing
        bcrhp/
            views/
                __init__.py            <- created if missing, never touched again
                generated/             <- fully owned by this command
            urls_api_generated.py      <- generated; include() from the
                                          urls_api_documented urlconf

Command layout:

    arches_zod_validation/management/commands/
        generate_graph_views.py        <- this file
        templates/
            graph_views.py-tpl
            __init__.py-tpl
            urls_api_generated.py-tpl
            openapi-ts.config.ts-tpl
"""

import importlib
import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.template import Context, Engine

from arches.app.models import models

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"

# Default verbs assigned to a newly-seen resource in views/generate.json.
# Hand-edit afterward; regeneration reads, never clobbers. An empty list ([])
# excludes the resource from generation.
DEFAULT_VERBS = ["GET"]


def slug_to_class_prefix(slug):
    """Convert a graph slug like ``heritage_site`` to ``HeritageSite``."""
    return "".join(part.capitalize() for part in slug.replace("-", "_").split("_"))


def view_bases(verbs):
    """Pick (list_base, detail_base) DRF generic class names from a resource's
    verbs: collection = GET (list) / POST (create); detail = GET (retrieve) /
    PUT,PATCH (update) / DELETE (destroy). Selects the base, not the
    persistence logic -- writes assume the mixins support create/update.
    """
    verbs = {v.upper() for v in verbs}
    has_get = "GET" in verbs

    if "POST" in verbs:
        list_base = "ListCreateAPIView" if has_get else "CreateAPIView"
    else:
        list_base = "ListAPIView"

    can_update = bool(verbs & {"PUT", "PATCH"})
    can_delete = "DELETE" in verbs
    if can_update and can_delete:
        detail_base = "RetrieveUpdateDestroyAPIView"
    elif can_update:
        detail_base = "RetrieveUpdateAPIView"
    elif can_delete:
        detail_base = "RetrieveDestroyAPIView"
    else:
        detail_base = "RetrieveAPIView"

    return list_base, detail_base


def normalize_path_prefix(value):
    """Normalize a user-supplied URL prefix to Django path() form.

    Accepts any slash variant -- '/bcrhp/', '/bcrhp', 'bcrhp', 'bcrhp/',
    even '//bcrhp//api/' -- and returns clean slash-separated segments with
    no leading or trailing slash ('bcrhp', 'bcrhp/api'). Returns '' for
    None, empty, or bare '/'.
    """
    if not value:
        return ""
    return "/".join(segment for segment in value.strip().split("/") if segment)


def default_project_name():
    """The Django project package name, e.g. ``bcrhp``.

    Arches projects set ``APP_NAME`` in settings; fall back to the first
    component of the settings module path (``bcrhp.settings`` -> ``bcrhp``).
    """
    app_name = getattr(settings, "APP_NAME", None)
    if app_name:
        return app_name
    return settings.SETTINGS_MODULE.split(".")[0]


def resolve_project_dir(project):
    """Locate the project package on disk by importing it.

    Resolving via import (never by joining a relative path to CWD)
    guarantees we write into the real package and can never create a
    stray directory that shadows it.
    """
    try:
        module = importlib.import_module(project)
    except ImportError as err:
        raise CommandError(
            f"Could not import project package '{project}': {err}"
        ) from err
    if not getattr(module, "__file__", None):
        raise CommandError(
            f"'{project}' resolves to a namespace package (no __init__.py) "
            "-- refusing to write into it. Check for a stray directory "
            "shadowing the real package."
        )
    return Path(module.__file__).resolve().parent


class Command(BaseCommand):
    # This command only writes files; it must work on a fresh project whose
    # ROOT_URLCONF may not import yet (e.g. it includes the not-yet-created
    # urls_api_generated module). Default system checks import the URLconf,
    # so they are skipped here.
    requires_system_checks = []

    help = (
        "Generate read-only, user-scoped DRF views (list + detail) for every "
        "resource graph into the project package, a urls_api_generated.py "
        "module for drf_spectacular, and a default openapi-ts.config.ts "
        "(only when missing)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--project",
            default=None,
            help=(
                "Dotted name of the Django project package to write into "
                "(default: auto-detected from settings, e.g. 'bcrhp')."
            ),
        )
        parser.add_argument(
            "--url-prefix",
            default="api",
            help=(
                "URL prefix for the generated routes. Any slash style is "
                "accepted and normalized. Default: api"
            ),
        )
        parser.add_argument(
            "--context-root",
            default="",
            help=(
                "Reverse-proxy context root to prepend to all generated "
                "routes, e.g. when the app is served at /bcrhp/. Accepts "
                "'/bcrhp/', '/bcrhp', 'bcrhp', or 'bcrhp/' interchangeably. "
                "Default: none."
            ),
        )
        parser.add_argument(
            "--schema-file",
            default="schema.yml",
            help=(
                "OpenAPI spec filename referenced by the scaffolded "
                "openapi-ts.config.ts. Default: schema.yml"
            ),
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print what would be generated without writing any files.",
        )
        parser.add_argument(
            "--overwrite",
            action="store_true",
            help=(
                "Overwrite existing generated view modules instead of "
                "skipping them. Never affects openapi-ts.config.ts."
            ),
        )

    def get_graphs(self):
        return (
            models.Graph.objects.order_by("slug")
            .filter(isresource=True)
            .exclude(slug="arches_system_settings")
            .exclude(source_identifier__isnull=False)
            .all()
        )

    def get_engine(self):
        """A standalone engine pointed at this command's template directory.

        Standalone (rather than ``loader.get_template``) so generation does not
        depend on, or interfere with, the project's TEMPLATES settings.
        Autoescaping is off because we render source code, not HTML.
        """
        return Engine(dirs=[str(TEMPLATE_DIR)], autoescape=False)

    def render(self, engine, template_name, context):
        template = engine.get_template(template_name)
        return template.render(Context(context, autoescape=False))

    def load_manifest(self, path):
        """Read the slug -> verbs map from generate.json (single
        manifest for all resources). Returns {} when absent; missing slugs are
        filled with defaults below. An empty verb list excludes the resource."""
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        return {}

    def write(self, path, content, dry_run):
        if dry_run:
            self.stdout.write(f"[dry-run] would write {path}")
        else:
            path.write_text(content, encoding="utf-8")
            self.stdout.write(self.style.SUCCESS(f"Wrote {path}"))

    def handle(self, *args, **options):
        project = options["project"] or default_project_name()
        schema_file = options["schema_file"]
        dry_run = options["dry_run"]
        overwrite = options["overwrite"]

        # Both inputs tolerate any slash style ('/bcrhp/', 'bcrhp', ...).
        # Composed result has no leading slash (Django path() requirement);
        # the urls template supplies the slashes between/after segments.
        context_root = normalize_path_prefix(options["context_root"])
        url_prefix = normalize_path_prefix(options["url_prefix"])
        full_prefix = "/".join(part for part in (context_root, url_prefix) if part)
        # Trailing slash carried here, not in the template, so an empty
        # prefix yields 'heritage_site/' rather than '/heritage_site/'
        # (Django path() routes must not start with a slash).
        route_prefix = f"{full_prefix}/" if full_prefix else ""

        project_dir = resolve_project_dir(project)
        views_dir = project_dir / "views"
        generated_dir = views_dir / "generated"

        # Guard: creating bcrhp/views/ beside an existing bcrhp/views.py would
        # shadow the module (packages win) and break every existing import.
        legacy_views = project_dir / "views.py"
        if legacy_views.exists() and not views_dir.is_dir():
            raise CommandError(
                f"{legacy_views} exists; creating {views_dir}/ would shadow "
                "it. Convert views.py into a views/ package first."
            )

        graphs = list(self.get_graphs())
        if not graphs:
            raise CommandError("No resource graphs found.")

        engine = self.get_engine()

        if not dry_run:
            generated_dir.mkdir(parents=True, exist_ok=True)
            # Make <project>/views a package if it is brand new, but never
            # clobber an existing hand-written views/__init__.py.
            views_init = views_dir / "__init__.py"
            if not views_init.exists():
                views_init.touch()
                self.stdout.write(self.style.SUCCESS(f"Wrote {views_init}"))

        entries = []  # shared context for __init__.py-tpl and urls template
        skipped = 0
        excluded = 0

        manifest_path = views_dir / "generate.json"
        manifest = self.load_manifest(manifest_path)

        for graph in graphs:
            slug = graph.slug
            # Missing slug -> default verbs; empty list -> excluded entirely
            # (no module, no url, no __init__ entry). Edit generate.json to opt
            # a resource in/out.
            verbs = manifest.setdefault(slug, list(DEFAULT_VERBS))
            if not verbs:
                self.stdout.write(f"Excluding {slug} (empty verbs in generate.json).")
                excluded += 1
                # Remove a previously-generated module so excluding a resource
                # takes it out of the package, not just the urls/__init__ wiring.
                stale = generated_dir / f"{slug}.py"
                if stale.exists():
                    if dry_run:
                        self.stdout.write(f"[dry-run] would delete {stale}")
                    else:
                        stale.unlink()
                        self.stdout.write(self.style.WARNING(f"Deleted {stale}"))
                continue
            class_prefix = slug_to_class_prefix(slug)
            list_base, detail_base = view_bases(verbs)
            entries.append(
                {
                    "module_name": slug,
                    "class_prefix": class_prefix,
                    "verbs": verbs,
                }
            )

            module_path = generated_dir / f"{slug}.py"
            if module_path.exists() and not overwrite:
                self.stdout.write(
                    self.style.WARNING(
                        f"Skipping {module_path} (exists; use --overwrite to replace)."
                    )
                )
                skipped += 1
                continue  # still wired into __init__ and urls below

            content = self.render(
                engine,
                "graph_views.py-tpl",
                {
                    "slug": slug,
                    "class_prefix": class_prefix,
                    "label": str(graph.name) or class_prefix,
                    "verbs": verbs,
                    "list_base": list_base,
                    "detail_base": detail_base,
                },
            )
            self.write(module_path, content, dry_run)

        if set(manifest) - set(self.load_manifest(manifest_path)):
            self.write(
                manifest_path,
                json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                dry_run,
            )

        all_names = sorted(
            f"{entry['class_prefix']}{suffix}"
            for entry in entries
            for suffix in ("Serializer", "ViewMixin", "ListView", "View")
        )
        init_content = self.render(
            engine,
            "__init__.py-tpl",
            {"entries": entries, "all_names": all_names},
        )
        self.write(generated_dir / "__init__.py", init_content, dry_run)

        urls_content = self.render(
            engine,
            "urls_api_generated.py-tpl",
            {"entries": entries, "project": project, "url_prefix": route_prefix},
        )
        self.write(project_dir / "urls_api_generated.py", urls_content, dry_run)

        # Scaffold openapi-ts.config.ts at the repo root (next to manage.py /
        # package.json, i.e. the project package's parent). Write-if-missing
        # only: this file is hand-tuned and must never be regenerated over.
        config_path = project_dir.parent / "openapi-ts.config.ts"
        if config_path.exists():
            self.stdout.write(f"Keeping existing {config_path} (never overwritten).")
        else:
            config_content = self.render(
                engine,
                "openapi-ts.config.ts-tpl",
                {
                    "schema_file": schema_file,
                    "client_path": f"{project}/src/{project}/client",
                },
            )
            self.write(config_path, config_content, dry_run)

        self.stdout.write(
            self.style.SUCCESS(
                f"Done. {len(entries)} graph(s) processed"
                + (f", {skipped} module(s) left untouched" if skipped else "")
                + (f", {excluded} excluded" if excluded else "")
                + ("." if not dry_run else " (dry run).")
            )
        )
        self.stdout.write(
            "Next steps (run each in its own process):\n"
            f"    python3.11 manage.py spectacular --urlconf {project}.urls_api_documented --file {schema_file}\n"
            "    npm run openapi:zod"
        )
