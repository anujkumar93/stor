import errno
import fnmatch
import glob
import logging
import ntpath
import os
import pkg_resources
import posixpath
import shutil
import sys
import warnings

from six.moves import builtins
from six import text_type
from six import string_types
from six import PY3

from stor import utils


logger = logging.getLogger(__name__)


def get_modules():
    modules = {}
    for entry_point in pkg_resources.iter_entry_points('stor.providers'):
        try:
            modules.update({entry_point.name: entry_point.load()})
        except pkg_resources.DistributionNotFound as e:  # pragma: no cover
            warnings.warn('Ignoring {entry_point} module as the requirement(s) '
                          'for the module are not installed. Exception : {exception}'
                          .format(entry_point=entry_point.name, exception=e))
            pass
    return modules


def get_class_for_path(path):
    module_map = get_modules()
    for k, v in module_map.items():
        if path.startswith(k + '://'):
            return v(k, path)
    return None, None


class TreeWalkWarning(Warning):
    pass


class Path(text_type):
    """
    Wraps path operations with an object-oriented API that makes it easier to
    combine and also to work with OBS and local paths via a single API. Methods
    on this class will be implemented by all subclasses of path.

    Using the class-level constructor returns a concrete subclass based on
    prefix and current environment.

    Examples::

        >>> from stor import Path
        >>> Path('/some/path')
        PosixPath('/some/path')
        >>> Path('swift://AUTH_something/cont/blah')
        SwiftPath('swift://AUTH_something/cont/blah')
        >>> Path('s3://bucket/prefix/key')
        S3Path('s3://bucket/prefix/key')
    """

    def __new__(cls, path):
        if cls is Path:
            if not hasattr(path, 'startswith'):
                raise TypeError('must be a string like')
            cls, obs_p = get_class_for_path(path)
            if cls is None:
                if os.path == ntpath:
                    from stor.windows import WindowsPath

                    cls = WindowsPath
                elif os.path == posixpath:
                    from stor.posix import PosixPath

                    cls = PosixPath
                else:  # pragma: no cover
                    assert False, 'path is not compatible with stor'
            else:
                path = obs_p
        return text_type.__new__(cls, path)

    def __init__(self, path):
        super(Path, self).__init__()

    @utils.ClassProperty
    @classmethod
    def path_class(cls):
        """What class should be used to construct new instances from this class"""
        return cls

    @utils.ClassProperty
    @classmethod
    def parts_class(cls):
        """What class should be used to construct path *components*"""
        return cls

    def _has_incompatible_path_module(self, other):
        """Returns true if the other path is a stor path and has a
        compatible path module for path operations"""
        return isinstance(other, Path) and other.path_module != self.path_module

    def copy(self, dest):
        """Copy Path to dest"""
        raise NotImplementedError

    def copytree(self, dest, use_manifest=False, headers=None,
                 condition=None, **kwargs):
        """Copytree Path dir to dest dir"""
        raise NotImplementedError

    def __repr__(self):
        return '%s(%s)' % (type(self).__name__, super(Path,
                           self).__repr__().lstrip('u'))

    def __div__(self, rel):
        """Join two path components (self / rel), adding a separator character if needed."""
        if self._has_incompatible_path_module(rel):
            return NotImplemented
        return self.path_class(self.path_module.join(self, rel))

    def __rdiv__(self, rel):
        """Join two path components (rel / self), adding a separator character if needed."""
        if self._has_incompatible_path_module(rel):
            return NotImplemented
        return self.path_class(self.path_module.join(rel, self))

    # Make the / operator work even when true division is enabled.
    __truediv__ = __div__
    __rtruediv__ = __rdiv__

    def __add__(self, more):
        if self._has_incompatible_path_module(more):
            return NotImplemented
        return self.path_class(super(Path, self).__add__(more))

    def __radd__(self, other):
        if self._has_incompatible_path_module(other):
            return NotImplemented
        if not isinstance(other, string_types):
            return NotImplemented
        return self.path_class(other.__add__(self))

    #
    # --- Operations on Path strings.

    def abspath(self):
        """See :func:`os.path.abspath` """
        return self.path_class(self.path_module.abspath(self))

    def normcase(self):
        """See :func:`os.path.normcase` """
        return self.path_class(self.path_module.normcase(self))

    def normpath(self):
        """See :func:`os.path.normpath` """
        return self.path_class(self.path_module.normpath(self))

    def realpath(self):  # pragma: no cover (temporary)
        """See :func:`os.path.realpath` """
        return self.path_class(self.path_module.realpath(self))

    def expanduser(self):
        """See :func:`os.path.expanduser` """
        return self.path_class(self.path_module.expanduser(self))

    def expandvars(self):
        """See :func:`os.path.expandvars` """
        return self.path_class(self.path_module.expandvars(self))

    def dirname(self):
        """See :attr:`parent`, :func:`os.path.dirname` """
        return self.path_class(self.path_module.dirname(self))

    def basename(self):
        """See :attr:`name`, :func:`os.path.basename` """
        return self.parts_class(self.path_module.basename(self))

    def expand(self):
        """ Clean up a filename by calling :meth:`expandvars()`,
        :meth:`expanduser()`, and :meth:`normpath()` on it.

        This is commonly everything needed to clean up a filename
        read from a configuration file, for example.
        """
        return self.expandvars().expanduser().normpath()

    def fnmatch(self, pattern, normcase=None):
        """Return ``True`` if :attr:`name` matches the given ``pattern``.

        .. seealso:: :func:`fnmatch.fnmatch`

        Args:
            pattern (str): A filename pattern with wildcards,
                for example ``'*.py'``. If the pattern contains a `normcase`
                attribute, it is applied to the name and path prior to comparison.
            normcase (func, optional): A function used to normalize the pattern and
                filename before matching. Defaults to :meth:`self.module`, which defaults
                to :meth:`os.path.normcase`.

        """
        default_normcase = getattr(pattern, 'normcase', self.path_module.normcase)
        normcase = normcase or default_normcase
        name = normcase(self.name)
        pattern = normcase(pattern)
        return fnmatch.fnmatchcase(name, pattern)

    @property
    def parent(self):
        return self.dirname()

    @property
    def name(self):
        return self.basename()

    @property
    def namebase(self):
        """ The same as :attr:`name`, but with one file extension stripped off.

        For example,
        ``Path('/home/guido/python.tar.gz').name == 'python.tar.gz'``,
        but
        ``Path('/home/guido/python.tar.gz').namebase == 'python.tar'``.
        """
        base, ext = self.path_module.splitext(self.name)
        return base

    @property
    def drive(self):
        return self.splitdrive()[0]

    @property
    def ext(self):
        """ The file extension, for example ``'.py'``. """
        return self.splitext()[1]

    def splitpath(self):
        """ p.splitpath() -> Return ``(p.parent, p.name)``.

        (naming is to avoid colliding with str.split)

        See: :attr:`parent`, :attr:`name`, :func:`os.path.split`
        """
        parent, child = self.path_module.split(self)
        return self.path_class(parent), child

    def splitext(self):
        """ p.splitext() -> Return ``(p.stripext(), p.ext)``.

        Split the filename extension from this path and return
        the two parts.  Either part may be empty.

        The extension is everything from ``'.'`` to the end of the
        last path segment.  This has the property that if
        ``(a, b) == p.splitext()``, then ``a + b == p``.

        See: :func:`os.path.splitext`
        """
        filename, ext = self.path_module.splitext(self)
        return self.path_class(filename), ext

    def splitdrive(self):
        """ p.splitdrive() -> Return ``(p.drive, <the rest of p>)``.

        Split the drive specifier from this path.  If there is
        no drive specifier, :samp:`{p.drive}` is empty, so the return value
        is simply ``(Path(''), p)``.  This is always the case on Unix.

        See: :func:`os.path.splitdrive`
        """
        drive, rel = self.path_module.splitdrive(self)
        return self.path_class(drive), rel

    def joinpath(self, *others):
        """
        Join first to zero or more :class:`Path` components, adding a separator
        character (:samp:`{first}.module.sep`) if needed.  Returns a new
        instance of :samp:`{first}.path_class`.

        See: :func:`os.path.join`
        """
        return self.path_class(self.path_module.join(self, *others))

    def open(self, mode=None, encoding=None):
        """Open a file-like object.

        The only cross-compatible arguments for this function are listed below.

        Args:
            mode (str): first positional arg, mode of file descriptor
            encoding (str): text encoding to use (Python 3 only)
        """

        raise NotImplementedError

    def list(self, *args, **kwargs):
        """List all contents using the path as a prefix.

        Note: Skips broken symlinks."""
        raise NotImplementedError

    def listdir(self):
        raise NotImplementedError

    def glob(self, pattern):
        """ Glob for pattern relative to this directory.

        Note that Swift currently only supports a single trailing *"""
        raise NotImplementedError

    def exists(self):
        """Checks whether path exists on local filesystem or on swift.

        For directories on swift, checks whether directory sentinel exists or
        at least one subdirectory exists"""
        raise NotImplementedError

    def isabs(self):
        """ See: :func:`os.path.isabs`

        Always True with SwiftPath"""
        raise NotImplementedError

    def isdir(self):
        """ See: :func:`os.path.isdir` """
        raise NotImplementedError

    def isfile(self):
        """ See: :func:`os.path.isfile` """
        raise NotImplementedError

    def islink(self):
        """ See: :func:`os.path.islink`

        Always False on Swift."""
        raise NotImplementedError

    def ismount(self):
        """ See: :func:`os.path.ismount`

        Always True on Swift.
        """
        raise NotImplementedError

    def getsize(self):
        """Returns size, in bytes of path.

        For Swift containers and tenants, will return 0. For POSIX directories,
        returns an undefined value.

        Raises:
            os.error: if file does not exist or is inaccessible
            NotFoundError/UnauthorizedError: from swift
        """
        raise NotImplementedError

    def remove(self):
        """ Delete single path or object """
        raise NotImplementedError

    def rmtree(self):
        """Delete entire directory (or all paths starting with prefix).

        See shutil.rmtree"""
        raise NotImplementedError

    def makedirs_p(self, mode=0o777):
        """ Like :func:`os.makedirs`, but does not raise an exception if the
        directory already exists. """
        raise NotImplementedError

    def walkfiles(self, pattern=None):
        """Iterate over files recursively.

        Args:
            pattern (str, optional): Limits the results to files
                with names that match the pattern.  For example,
                ``mydir.walkfiles('*.tmp')`` yields only files with the ``.tmp``
                extension.

        Returns:
            Iter[Path]: Files recursively under the path
        """
        raise NotImplementedError


class FileSystemPath(Path):
    """'Abstract' class implementing file-system specific operations.

    In particular: allows changing directory when used as contextmanager and
    wraps Python's builtin open() to be compatible with swift_upload_args.
    """

    def copy(self, dest, **swift_retry_options):
        """Copies a source file to a destination file.

        Note that this utility can be called from either OBS, posix, or
        windows paths created with ``stor.Path``.

        Args:
            self (Path|str): The source directory to copy from
            dest (Path|str): The destination file or directory.
            swift_retry_options (dict): Optional retry arguments to use for swift
                upload or download. View the
                `swift module-level documentation <swiftretry>` for more
                information on retry arguments

        Examples:
            Copying a swift file to a local path behaves as follows::

                >>> import stor
                >>> swift_p = 'swift://tenant/container/dir/file.txt'
                >>> # file.txt will be copied to other_dir/other_file.txt
                >>> stor.copy(swift_p, 'other_dir/other_file.txt')

            Copying from a local path to swift behaves as follows::

                >>> from stor import Path
                >>> local_p = Path('my/local/file.txt')
                >>> # File will be uploaded to swift://tenant/container/dir/my_file.txt
                >>> local_p.copy('swift://tenant/container/dir/')

            Because of the ambiguity in whether a remote target is a file or directory, copy()
            will error on ambiguous paths.

                >>> local_p.copy('swift://tenant/container/dir')
                Traceback (most recent call last):
                ...
                ValueError: OBS destination must be file with extension or directory with slash
        """
        from stor.obs import OBSUploadObject
        dest = Path(dest)

        if utils.is_filesystem_path(dest):
            dest.parent.makedirs_p()
            shutil.copy(self, dest)
        else:
            dest_file = dest if not dest.endswith('/') else dest / self.name
            dest_obj_name = Path(dest_file.parent.resource or '') / dest_file.name
            upload_obj = OBSUploadObject(self, dest_obj_name)
            dest_file._copy_upload([upload_obj],
                                   **swift_retry_options)

    def copytree(self, dest, use_manifest=False, headers=None,
                 condition=None, **kwargs):
        """Copies a source directory to a destination directory. Assumes that
        paths are capable of being copied to/from.

        Note that this function uses shutil.copytree by default, meaning
        that a posix or windows destination must not exist beforehand.

        For example, assume the following file hierarchy::

            a/
            - b/
            - - 1.txt

        Doing a copytree from ``a`` to a new posix destination of ``c`` is
        performed with::

            Path('a').copytree('c')

        The end result for c looks like::

            c/
            - b/
            - - 1.txt

        Note that the user can override which copy command is used for posix
        copies, and it is up to them to ensure that their code abides by the
        semantics of the provided copy command. This function has been tested
        in production using the default command of ``cp -r`` and using ``mcp -r``.

        Using OBS source and destinations work in a similar manner. Assume
        the destination is a swift path and we upload the same ``a`` folder::

            Path('a').copytree('swift://tenant/container/folder')

        The end swift result will have one object::

            Path('swift://tenant/container/folder/b/1.txt')

        Similarly one can do::

            Path('swift://tenant/container/folder/').copytree('c')

        The end result for c looks the same as the above posix example::

            c/
            - b/
            - - 1.txt

        Args:
            self (Path|str): The source directory to copy from
            dest (Path|str): The directory to copy to. Must not exist if
                its a posix directory
            use_manifest (bool, default False): See `SwiftPath.upload` and
                `SwiftPath.download`.
            condition (function(results) -> bool): See `SwiftPath.upload` and
                `SwiftPath.download`.
            headers (List[str]): See `SwiftPath.upload`.

        Raises:
            ValueError: if two OBS paths are specified
            OSError: if destination is a posix path and it already exists
        """
        dest = Path(dest)
        if utils.is_filesystem_path(dest):
            dest.expand().abspath().parent.makedirs_p()
            shutil.copytree(self, dest)
        else:
            dest._copytree_upload(self, use_manifest=use_manifest, headers=headers,
                                  condition=condition, **kwargs)

    def open(self, mode=None, encoding=None):
        """
        Opens a path and retains interface compatibility with
        `SwiftPath` by popping the unused ``swift_upload_args`` keyword
        argument.

        Creates parent directory if it does not exist.
        """
        self.parent.makedirs_p()
        # only set kwargs if they aren't set to avoid overwriting defaults
        kwargs = {k: v for k, v in [('mode', mode), ('encoding', encoding)] if v}
        return builtins.open(self, **kwargs)

    def __enter__(self):
        self._old_dir = os.getcwd()
        os.chdir(self)
        return self

    def __exit__(self, *_):
        os.chdir(self._old_dir)

    def chdir(self):
        os.chdir(self)

    # no stat() because we want to provide a cross-compatible API at some point

    @staticmethod
    def _always_unicode(path):  # pragma: no cover (OS-dependent)
        """
        Ensure the path as retrieved from a Python API, such as
        :func:`os.listdir`, is a proper Unicode string.
        """
        if PY3 or isinstance(path, text_type):
            return path
        return path.decode(sys.getfilesystemencoding(), 'surrogateescape')

    def listdir(self):
        """D.listdir() -> List of items in this directory.

        The elements of the list are Path objects.
        """
        return [self / child
                for child in map(self._always_unicode, os.listdir(self))]

    def glob(self, pattern):
        cls = self.path_class
        return [cls(s) for s in glob.glob(self / pattern)]

    def exists(self):
        """ See: :func:`os.path.exists` """
        return self.path_module.exists(self)

    def isabs(self):
        """ See: :func:`os.path.isabs` """
        return self.path_module.isabs(self)

    def isdir(self):
        """ See: :func:`os.path.isdir` """
        return self.path_module.isdir(self)

    def isfile(self):
        """ See: :func:`os.path.isfile` """
        return self.path_module.isfile(self)

    def islink(self):  # pragma: no cover (temporary)
        """ See: :func:`os.path.islink` """
        return self.path_module.islink(self)

    def ismount(self):  # pragma: no cover (temporary)
        """ See: :func:`os.path.ismount` """
        return self.path_module.ismount(self)

    def getsize(self):
        """ See: :func:`os.path.getsize` """
        return self.path_module.getsize(self)

    def remove(self):
        """ See: :func:`os.remove` """
        os.remove(self)
        return self

    rmtree = shutil.rmtree

    def makedirs(self, mode=0o777):
        """ See: :func:`os.makedirs` """
        os.makedirs(self, mode)
        return self

    def makedirs_p(self, mode=0o777):
        """ Like :meth:`makedirs`, but does not raise an exception if the
        directory already exists. """
        if not self:
            # no point in trying to make an empty relative path :)
            return
        try:
            self.makedirs(mode)
        except OSError:
            _, e, _ = sys.exc_info()
            if e.errno != errno.EEXIST:  # pragma: no cover (temporary)
                raise
        return self

    def _splitall(self):  # pragma: no cover (temporary)
        r""" Return a list of the path components in this path.

        The first item in the list will be a Path.  Its value will be
        either :data:`os.curdir`, :data:`os.pardir`, empty, or the root
        directory of this path (for example, ``'/'`` or ``'C:\\'``).  The
        other items in the list will be strings.

        ``path.Path.joinpath(*result)`` will yield the original path.
        """
        parts = []
        loc = self
        while loc != os.curdir and loc != os.pardir:
            prev = loc
            loc, child = prev.splitpath()
            if loc == prev:
                break
            parts.append(child)
        parts.append(loc)
        parts.reverse()
        return parts

    def relpath(self, start='.'):
        """ Return this path as a relative path,
        based from `start`, which defaults to the current working directory.
        """
        cwd = self.path_class(start)
        return cwd.relpathto(self)

    def relpathto(self, dest):  # pragma: no cover (temporary)
        """ Return a relative path from `self` to `dest`.

        If there is no relative path from `self` to `dest`, for example if
        they reside on different drives in Windows, then this returns
        ``dest.abspath()``.
        """
        origin = self.abspath()
        dest = self.path_class(dest).abspath()

        orig_list = origin.normcase()._splitall()
        # Don't normcase dest!  We want to preserve the case.
        dest_list = dest._splitall()

        if orig_list[0] != self.path_module.normcase(dest_list[0]):
            # Can't get here from there.
            return dest

        # Find the location where the two paths start to differ.
        i = 0
        for start_seg, dest_seg in zip(orig_list, dest_list):
            if start_seg != self.path_module.normcase(dest_seg):
                break
            i += 1

        # Now i is the point where the two paths diverge.
        # Need a certain number of "os.pardir"s to work up
        # from the origin to the point of divergence.
        segments = [os.pardir] * (len(orig_list) - i)
        # Need to add the diverging part of dest_list.
        segments += dest_list[i:]
        if len(segments) == 0:
            # If they happen to be identical, use os.curdir.
            relpath = os.curdir
        else:
            relpath = self.path_module.join(*segments)
        return self.path_class(relpath)

    def mkdir(self, mode=0o777):
        """ .. seealso:: :func:`os.mkdir` """
        os.mkdir(self, mode)
        return self

    def mkdir_p(self, mode=0o777):
        """ Like :meth:`mkdir`, but does not raise an exception if the
        directory already exists. """
        try:
            self.mkdir(mode)
        except OSError:
            _, e, _ = sys.exc_info()
            if e.errno != errno.EEXIST:  # pragma: no cover (temporary)
                raise
        return self

    def rmdir(self):
        """ .. seealso:: :func:`os.rmdir` """
        os.rmdir(self)
        return self

    def rmdir_p(self):
        """ Like :meth:`rmdir`, but does not raise an exception if the
        directory is not empty or does not exist. """
        try:
            self.rmdir()
        except OSError:  # pragma: no cover (temporary)
            _, e, _ = sys.exc_info()
            if e.errno != errno.ENOTEMPTY and e.errno != errno.ENOENT:
                raise
        return self

    def walkfiles(self, pattern=None, errors='strict'):  # flake8: noqa pragma: no cover 
        """ D.walkfiles() -> iterator over files in D, recursively.
        The optional argument `pattern` limits the results to files
        with names that match the pattern.  For example,
        ``mydir.walkfiles('*.tmp')`` yields only files with the ``.tmp``
        extension.
        """
        if errors not in ('strict', 'warn', 'ignore'):
            raise ValueError("invalid errors parameter")

        try:
            childList = self.listdir()
        except Exception:
            if errors == 'ignore':
                return
            elif errors == 'warn':
                warnings.warn(
                    "Unable to list directory '%s': %s"
                    % (self, sys.exc_info()[1]),
                    TreeWalkWarning)
                return
            else:
                raise

        for child in childList:
            try:
                isfile = child.isfile()
                isdir = not isfile and child.isdir()
            except:
                if errors == 'ignore':
                    continue
                elif errors == 'warn':
                    warnings.warn(
                        "Unable to access '%s': %s"
                        % (self, sys.exc_info()[1]),
                        TreeWalkWarning)
                    continue
                else:
                    raise

            if isfile:
                if pattern is None or child.fnmatch(pattern):
                    yield child
            elif isdir:
                for f in child.walkfiles(pattern, errors):
                    yield f
