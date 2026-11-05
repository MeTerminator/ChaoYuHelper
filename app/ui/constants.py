"""UI labels and platform activity metadata."""

PLATFORMS = {"chaoxing": "学习通", "rainClassroom": "雨课堂"}
SERVERS = {"yuketang": "雨课堂", "pro": "荷塘雨课堂", "changjiang": "长江雨课堂", "huanghe": "黄河雨课堂"}
TYPES = {
    2: ("签到", "fact_check"),
    74: ("签退", "logout"),
    54: ("定时签到", "schedule"),
    4: ("抢答", "front_hand"),
    5: ("主题讨论", "forum"),
    11: ("选人", "people"),
    14: ("问卷", "assignment"),
    19: ("作业", "assignment_turned_in"),
    23: ("评分", "star_outline"),
    42: ("随堂练习", "edit_note"),
    43: ("投票", "poll"),
}
SIGN_TYPES = {"0": "普通签到", "2": "二维码签到", "3": "手势签到", "4": "位置签到", "5": "签到码签到"}
