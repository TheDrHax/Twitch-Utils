"""Usage: twitch_utils <command> [<args>...]

Commands:
  concat  Concatenate multiple MPEG-TS files
  offset  Calculate offset between two audio (or video) files
  mute    Remove background music from video
  record  Record live Twitch stream from the beginning
"""

from docopt import docopt


def main(argv=None):
    args = docopt(__doc__, argv=argv, options_first=True)

    argv = [args['<command>']] + args['<args>']

    if args['<command>'] == 'concat':
        from .concat import main as concat_main
        concat_main(argv)
    elif args['<command>'] == 'offset':
        from .offset import main as offset_main
        offset_main(argv)
    elif args['<command>'] == 'mute':
        from .mute import main as mute_main
        mute_main(argv)
    elif args['<command>'] == 'record':
        from .record import main as record_main
        record_main(argv)


if __name__ == '__main__':
    main()
