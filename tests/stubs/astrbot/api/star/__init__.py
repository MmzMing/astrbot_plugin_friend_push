class Context:
    pass


class Star:
    def __init__(self, context=None, config=None):
        self.context = context
        self.config = config

    async def terminate(self):
        pass
