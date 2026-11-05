"""Browser UI public entry point; importing registers the page routes."""

from .components import field as field
from .components import guarded as guarded
from .components import homework_rich as homework_rich
from .components import options_for as options_for
from .components import primary as primary
from .components import rich as rich
from .constants import PLATFORMS as PLATFORMS
from .constants import SERVERS as SERVERS
from .constants import SIGN_TYPES as SIGN_TYPES
from .constants import TYPES as TYPES
from .dashboard import Dashboard as Dashboard
from .routes import (
    homework_answer_page as homework_answer_page,
)
from .routes import (
    homework_page as homework_page,
)
from .routes import (
    homework_review_page as homework_review_page,
)
from .routes import (
    index as index,
)
from .routes import (
    render_homework_route as render_homework_route,
)
