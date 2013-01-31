import os.path
import shlex
import subprocess
from fnmatch import fnmatch
from optparse import make_option
from django.conf import settings
from django.contrib.staticfiles import finders
from django.core.management.base import BaseCommand
from django.utils.datastructures import SortedDict


def find_static_files(ignore_patterns=()):
    found_files = SortedDict()
    for finder in finders.get_finders():
        for path, storage in finder.list(ignore_patterns):
            found_files[path] = storage.path(path)
    return found_files


class Command(BaseCommand):
    option_list = BaseCommand.option_list + (
        make_option('--no-compile', action='store_false', default=True, dest='compile'),
    )

    def get_format_params(self, dst):
        filename = os.path.basename(dst)
        path = os.path.dirname(dst)
        basename, ext = os.path.splitext(filename)
        if path.startswith(settings.STATIC_ROOT):
            relpath = path[len(settings.STATIC_ROOT) + 1:]
        else:
            relpath = path

        return dict(
            name=basename,
            ext=ext,
            filename=filename,
            relpath=relpath,
            abspath=path,
            static_root=os.path.abspath(settings.STATIC_ROOT),
        )

    def parse_command(self, cmd, **params):
        parsed_cmd = shlex.split(str(cmd).format(**params))
        # force absolute path to binary
        parsed_cmd[0] = os.path.abspath(parsed_cmd[0])

        # TODO: why is uglify hanging when we pass the command as a list?
        return ' '.join(parsed_cmd)

    def run_command(self, cmd, root, dst, **params):
        """
        Execute a command, and if successful write it's stdout to ``root``/``dst``.
        """
        parsed_cmd = self.parse_command(cmd, **params)

        print " ->", parsed_cmd
        proc = subprocess.Popen(
            args=parsed_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=True,
            cwd=root,
        )
        (stdout, stderr) = proc.communicate()

        assert not proc.returncode, stderr

        # TODO: this should probably change dest to be a temp file
        with open(os.path.join(root, dst), 'w') as fp:
            fp.write(stdout)

    def apply_preprocessors(self, root, src, dst, processors):
        """
        Preprocessors operate based on the source filename, and apply to each
        file individually.
        """
        matches = [(pattern, cmds) for pattern, cmds in processors.iteritems() if fnmatch(src, pattern)]
        if src == dst and not matches:
            return

        params = self.get_format_params(dst)

        src_path = src
        for pattern, cmd_list in matches:
            for cmd in cmd_list:
                self.run_command(cmd, root=root, dst=dst, input=src_path, **params)
                src_path = dst

    def apply_postcompilers(self, root, src_list, dst, processors):
        """
        Postcompilers operate based on the destination filename. They operate on a collection
        of files, and are expected to take a list of 1+ inputs and generate a single output.
        """
        dst_file = os.path.join(root, dst)

        matches = [(pattern, cmds) for pattern, cmds in processors.iteritems() if fnmatch(dst, pattern)]
        if not matches:
            # We should just concatenate the files
            with open(dst_file, 'w') as dst_fp:
                for src in src_list:
                    with open(os.path.join(root, src)) as src_fp:
                        for chunk in src_fp:
                            dst_fp.write(chunk)
            return

        params = self.get_format_params(dst)

        # TODO: probably doesnt play nice everywhere
        src_names = src_list
        for pattern, cmd_list in processors.iteritems():
            for cmd in cmd_list:
                self.run_command(cmd, root=root, dst=dst, input=' '.join(src_names), **params)
                src_names = [dst]

    def handle(self, *bundles, **options):
        config = settings.STATIC_BUNDLES
        if not config:
            return

        # First we need to build a mapping of all files using django.contrib.staticfiles
        bundle_mapping = {}
        for bundle_name, bundle_opts in config.get('packages', {}).iteritems():
            if bundles and bundle_name not in bundles:
                continue

            bundle_opts['ext'] = os.path.splitext(bundle_name)[1]
            bundle_opts.setdefault('preprocessors', config.get('preprocessors'))
            bundle_opts.setdefault('postcompilers', config.get('postcompilers'))
            bundle_mapping[bundle_name] = bundle_opts

        for bundle_name, bundle_opts in bundle_mapping.iteritems():
            src_outputs = []
            is_mapping = isinstance(bundle_opts['src'], dict)

            for src_path in bundle_opts['src']:
                if is_mapping:
                    dst_path = bundle_opts['src'][src_path]
                else:
                    dst_path = src_path

                self.apply_preprocessors(
                    settings.STATIC_ROOT,
                    src_path,
                    dst_path,
                    bundle_opts.get('preprocessors'),
                )

                src_outputs.append(dst_path)

            if options['compile']:
                self.apply_postcompilers(
                    settings.STATIC_ROOT,
                    src_outputs,
                    os.path.join(settings.STATIC_ROOT, bundle_name),
                    bundle_opts.get('postcompilers'),
                )
