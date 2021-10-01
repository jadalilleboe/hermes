import argparse
import inspect
from collections import abc
from functools import wraps
from typing import Callable, Optional, Tuple, Union


class _DictParsingAction(argparse.Action):
    """Action for parsing dictionary arguments

    Parse dictionary arguments using the form `key=value`,
    with the `type` argument specifying the type of `value`.
    The type of `key` must be a string. Alternatively, if
    a single argument is passed without `=` in it, it will
    be set as the value of the flag using `type`.

    Example ::

        parser = argparse.ArgumentParser()
        parser.add_argument("--a", type=int, action=_DictParsingAction)
        args = parser.parse_args(["--a", "foo=1", "bar=2"])
        assert args.a["foo"] == 1
    """

    def __init__(self, *args, **kwargs) -> None:
        self._type = kwargs["type"]
        kwargs["type"] = str
        super().__init__(*args, **kwargs)

    def __call__(self, parser, namespace, values, option_string=None) -> None:
        if len(values) == 1 and "=" not in values[0]:
            setattr(namespace, self.dest, self._type(values[0]))
            return

        dict_value = {}
        for value in values:
            k, v = value.split("=")
            dict_value[k] = self._type(v)
        setattr(namespace, self.dest, dict_value)


def _enforce_array_like(annotation, type_, name):
    annotation = annotation.__args__[1]

    # for array-like origins, make sure that
    # the type of the elements of the array
    # matches the type of the first element
    # of the Union. Otherwise, we don't know
    # how to parse the value
    try:
        if annotation.__origin__ in (list, abc.Sequence):
            assert annotation.__args__[0] is type_
        elif annotation.__origin__ is dict:
            assert annotation.__args__[1] is type_
        elif annotation.__origin__ is tuple:
            for arg in annotation.__args__:
                assert arg is type_
        else:
            raise TypeError(
                "Arg {} has Union of type {} and type {} "
                "with unknown origin {}".format(
                    name, type_, annotation, annotation.__origin__
                )
            )
    except AttributeError:
        raise TypeError(
            "Arg {} has Union of types {} and {}".format(
                name, type_, annotation
            )
        )
    return annotation


def _parse_union(param):
    annotation = param.annotation
    type_ = annotation.__args__[0]

    try:
        if isinstance(None, annotation.__args__[1]):
            # this is basically a typing.Optional case
            # make sure that the default is None
            if param.default is not None:
                raise ValueError(
                    "Argument {} with Union of type {} and "
                    "NoneType must have a default of None".format(
                        param.name, type_
                    )
                )
            annotation = type_
        else:
            annotation = _enforce_array_like(annotation, type_, param.name)
    except TypeError as e:
        if "Subscripted" in str(e):
            annotation = _enforce_array_like(annotation, type_, param.name)
        else:
            raise

    return annotation


def _get_origin_and_type(
    annotation: type,
) -> Tuple[Optional[type], Optional[type]]:
    """Utility for parsing the origin of an annotation

    Returns:
        If the annotation has an origin, this will be that origin.
            Otherwise it will be `None`
        If the annotation does not have an origin, this will
            be the annotation. Otherwise it will be `None`
    """

    try:
        origin = annotation.__origin__
        type_ = None
    except AttributeError:
        # annotation has no origin, so assume it's
        # a valid type on its own
        origin = None
        type_ = annotation
    return origin, type_


def _parse_array_like(
    annotation: type, origin: Optional[type], kwargs: dict
) -> Optional[type]:
    """Make sure array-like typed arguments pass the right type to the parser

    For an annotation with an origin, do some checks on the
    origin to make sure that the type and action argparse
    uses to parse the argument is correct. If the annotation
    doesn't have an origin, returns `None`.

    Args:
        annotation:
            The annotation for the argument
        origin:
            The origin of the annotation, if it exists,
            otherwise `None`
        kwargs:
            The dictionary of keyword arguments to be
            used to add an argument to the parser
    """

    if origin in (list, tuple, dict, abc.Sequence):
        kwargs["nargs"] = "+"
        if origin is dict:
            kwargs["action"] = _DictParsingAction

            # make sure that the expected type
            # for the dictionary key is string
            # TODO: add kwarg for parsing non-int
            # dictionary keys
            assert annotation.__args__[0] is str

            # the type used to parse the values for
            # the dictionary will be the type passed
            # the parser action
            type_ = annotation.__args__[1]
        else:
            type_ = annotation.__args__[0]

            # for tuples make sure that everything
            # has the same type
            if origin is tuple:
                # TODO: use a custom action to verify
                # the number of arguments and map to
                # a tuple
                for arg in annotation.__args__[1:]:
                    if arg is not Ellipsis:
                        assert arg == type_

        return type_
    elif origin is not None:
        # this is a type with some unknown origin
        raise TypeError(f"Can't help with arg of type {origin}")


def _parse_help(args: str, arg_name: str) -> str:
    """Find the help string for an argument

    Search through the `Args` section of a function's
    doc string for the lines describing a particular
    argument. Returns the empty string if no description
    is found

    Args:
        args:
            The arguments section of a function docstring.
            Should be formatted like
            ```
            '''
            arg1:
                The description for arg1
            arg2:
                The description for arg 2 that
                spans multiple lines
            arg3:
                Another description
            '''
            ```
            With 8 spaces before each argument name
            and 12 before the lines of its description.
        arg_name:
            The name of the argument whose help string
            to search for
    Returns:
        The help string for the argument with leading
        spaces stripped for each line and newlines
        replaced by spaces
    """

    doc_str, started = "", False
    for line in args.split("\n"):
        # TODO: more robustness on spaces
        if line == (" " * 8 + arg_name + ":"):
            started = True
        elif not line.startswith(" " * 12) and started:
            break
        elif started:
            doc_str += " " + line.strip()
    return doc_str


def make_parser(f: Callable, prog: str = None):
    """Build an argument parser for a function

    Builds an `argparse.ArgumentParser` object by using
    the arguments to a function `f`, as well as their
    annotations and docstrings (for help printing).
    The type support for annotations is pretty limited
    and needs more documenting here, but for a better
    idea see the unit tests in `../tests/unit/test_typeo.py`.

    Args:
        f:
            The function to construct a command line
            argument parser for
        prog:
            Passed to the `prog` argument of
            `argparse.ArgumentParser`. If left as None,
            `f.__name__` will be used
    Returns:
        The argument parser for the given function
    """

    # start by grabbing the function description
    # and any arguments that might have been
    # described in the docstring
    try:
        # split thet description and the args
        # by the expected argument section header
        doc, args = f.__doc__.split("Args:\n")
    except AttributeError:
        # raised if f doesn't have documentation
        doc, args = "", ""
    except ValueError:
        # raised if f only has a description but
        # no argument documentation. Set `args`
        # to the empty string
        doc, args = f.__doc__, ""
    else:
        # try to strip out any returns from the
        # arguments section by using the expected
        # returns header. If there are None, just
        # keep moving
        try:
            args, _ = args.split("Returns:\n")
        except ValueError:
            pass

    # build the parser, using a raw text formatter, so that
    # any formatting in the argument description is respected
    parser = argparse.ArgumentParser(
        prog=prog or f.__name__,
        description=doc.rstrip(),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # now iterate through the arguments of f
    # and add them as options to the parser
    for name, param in inspect.signature(f).parameters.items():
        annotation = param.annotation
        kwargs = {}

        # check to see if the annotation represents
        # a type that can be used by the parser, or
        # represents some container that needs
        # further parsing
        origin, type_ = _get_origin_and_type(annotation)

        # if the annotation can have multiple types,
        # figure out which type to pass to the parser
        if origin is Union:
            annotation = _parse_union(param)

            # check the chosen type again to
            # see if it's a container of some kind
            origin, type_ = _get_origin_and_type(annotation)

        # if the origin of the annotation is array-like,
        # indicate that there will be multiple args in the kwargs
        # and return the appropriate type. This returns `None`
        # if there's no origin to process, in which case we just
        # keep using `type_`
        type_ = _parse_array_like(annotation, origin, kwargs) or type_

        # add the argument docstring to the parser help
        kwargs["help"] = _parse_help(args, name)

        if type_ is bool:
            if param.default is inspect._empty:
                # if the argument is a boolean and doesn't
                # provide a default, assume that setting it
                # as a flag indicates a `True` status
                kwargs["action"] = "store_true"
            else:
                # otherwise set the action to be the
                # _opposite_ of whatever the default is
                # so that if it's not set, the default
                # becomes the values
                action = str(not param.default).lower()
                kwargs["action"] = f"store_{action}"
        else:
            kwargs["type"] = type_

            # args without default are required,
            # otherwise pass the default to the parser
            if param.default is inspect._empty:
                kwargs["required"] = True
            else:
                kwargs["default"] = param.default

        # use dashes instead of underscores for
        # argument names
        name = name.replace("_", "-")
        parser.add_argument(f"--{name}", **kwargs)
    return parser


def typeo(*args, **kwargs) -> Callable:
    """Function wrapper for passing command line args to functions

    Builds a command line parser for the arguments
    of a function so that if it is called without
    any arguments, its arguments will be attempted
    to be parsed from `sys.argv`.

    Usage:
        If your file `adder.py` looks like ::

            from hermes.typeo import typeo


            @typeo
            def f(a: int, other_number: int = 1) -> int:
                '''Adds two numbers together

                Longer description of the process of adding
                two numbers together.

                Args:
                    a:
                        The first number to add
                    other_number:
                        The other number to add whose description
                        inexplicably spans multiple lines
                '''

                print(a + other_number)


            if __name__ == "__main__":
                f()

        Then from the command line (note that underscores
        get replaced by dashes!) ::
            $ python adder.py --a 1 --other-number 2
            3
            $ python adder.py --a 4
            5
            $ python adder.py -h
            usage: f [-h] --a A [--other-number OTHER_NUMBER]

            Adds two numbers together

                Longer description of the process of adding
                two numbers together.

            optional arguments:
              -h, --help            show this help message and exit
              --a A                 The first number to add
              --other-number OTHER_NUMBER
                                    The other number to add whose description inexplicably spans multiple lines  # noqa

    Args:
        f:
            The function to expose via a command line parser
        prog:
            The name to assign to command line parser `prog`
            argument. If not provided, `f.__name__` will
            be used.
    """

    # the only argument is the function itself,
    # so just treat this like a simple wrapper
    if len(args) == 1 and isinstance(args[0], Callable):
        f = args[0]
        parser = make_parser(f)

        @wraps(f)
        def wrapper(*args, **kwargs):
            if len(args) == len(kwargs) == 0:
                kwargs = vars(parser.parse_args())
            return f(*args, **kwargs)

        return wrapper
    else:
        # we provided arguments to typeo above the
        # decorated function, so wrap the wrapper
        # using the provided arguments

        @wraps(typeo)
        def wrapperwrapper(f):
            parser = make_parser(f, *args, **kwargs)

            # now build the regular wrapper for f
            @wraps(f)
            def wrapper(*args, **kwargs):
                if len(args) == len(kwargs) == 0:
                    kwargs = vars(parser.parse_args())
                return f(*args, **kwargs)

            return wrapper

        return wrapperwrapper