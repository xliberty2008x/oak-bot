"""Public publisher routes. The host may supply its own topics and publishers."""

from .proactive_context import DiscoveryTopic
from .proactive_sources import feed_url


DEFAULT_CATALOGUE = (
    DiscoveryTopic('Video creation', 'https://blog.youtube/rss/',
                   ('youtube', 'ютуб*', 'відеомонтаж*', 'відеотвор*', 'video editing', 'making videos', 'shorts'),
                   ('creator*', 'shorts', 'video editing', 'storytell*', 'filmmak*')),
    DiscoveryTopic('Space exploration', 'https://www.nasa.gov/feed/',
                   ('astronomy', 'space exploration', 'spacecraft', 'astronom*', 'nasa', 'космос*', 'астроном*', 'voyager'),
                   ('spacecraft', 'mission*', 'telescope*', 'astronaut*', 'voyager', 'planet*')),
    DiscoveryTopic('Creative electronics', 'https://www.raspberrypi.com/news/feed/',
                   ('raspberry pi', 'robotics', 'physical computing', 'робототехнік*', 'мікроконтролер*'),
                   ('robot*', 'make', 'build*', 'project*', 'mechatronic*', 'raspberry pi')),
    DiscoveryTopic('Digital illustration', 'https://krita.org/en/posts/index.xml',
                   ('krita', 'digital painting', 'цифровий малюнок', 'digital illustration'),
                   ('krita', 'brush*', 'painting', 'tutorial*')),
    DiscoveryTopic('Python development', 'https://blog.python.org/feeds/posts/default',
                   ('python', 'cpython', 'пайтон*'),
                   ('python', 'cpython', 'release*')),
    DiscoveryTopic('Earthquake observations', 'https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_day.atom',
                   ('seismolog*', 'earthquake*', 'сейсмолог*', 'землетрус*'),
                   ('earthquake*', 'depth', 'magnitude')),
)


def load_catalogue(value=None):
    """An explicit list replaces the default routes. An empty list disables scanning."""
    if value is None:
        return DEFAULT_CATALOGUE
    if not isinstance(value, list) or len(value) > 24:
        raise ValueError('Provide a list of at most 24 discovery topics.')
    result = []
    names = set()
    for row in value:
        required = {'topic', 'source', 'context_terms', 'item_terms'}
        if not isinstance(row, dict) or not required <= row.keys() or row.keys() - required - {'reason'}:
            raise ValueError('Invalid discovery catalogue entry.')
        if not isinstance(row['topic'], str) or not isinstance(row.get('reason', ''), str):
            raise ValueError('Invalid discovery topic description.')
        feed_url(row['source'])
        for key in ('context_terms', 'item_terms'):
            if (not isinstance(row[key], list) or not 1 <= len(row[key]) <= 32
                    or not all(isinstance(term, str) and term.isprintable() for term in row[key])):
                raise ValueError('Provide 1 to 32 terms for each discovery topic.')
        topic = DiscoveryTopic(row['topic'].strip(), row['source'], tuple(row['context_terms']),
                               tuple(row['item_terms']), row.get('reason', ''))
        if topic.topic.casefold() in names:
            raise ValueError('Discovery topic names must be unique.')
        names.add(topic.topic.casefold())
        result.append(topic)
    return tuple(result)
