"""Official homework parsing public interface; no platform requests or script execution."""

from .attachments import attachment_html as attachment_html
from .attachments import saved_media as saved_media
from .constants import ANSWER_PATHS as ANSWER_PATHS
from .constants import HOST as HOST
from .constants import LIST_PATHS as LIST_PATHS
from .controls import dropdown_controls as dropdown_controls
from .controls import initial_answer as initial_answer
from .forms import merge_forms as merge_forms
from .forms import parse_form as parse_form
from .pages import document as document
from .pages import homework_url as homework_url
from .pages import page_links as page_links
from .pages import parse_list as parse_list
from .pages import review as review
from .submission import submission as submission
