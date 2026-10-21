import sys
from dub007.release import main
if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        print('BLAD: '+str(error), file=sys.stderr)
        raise SystemExit(1)
