class _Filter:
    @staticmethod
    def command(name=None, alias=None, **kwargs):
        def deco(fn):
            return fn

        return deco

    @staticmethod
    def regex(pattern, **kwargs):
        def deco(fn):
            return fn

        return deco


filter = _Filter()


class AstrMessageEvent:
    pass


class MessageEventResult:
    pass
