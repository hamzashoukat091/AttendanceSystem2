import os
from datetime import datetime, timedelta
from django.conf import settings
from .models import Attendance

def mark_user_attendance(user):
    today = datetime.today().date()
    now_time = datetime.now()

    # Get or create today's record
    attendance, created = Attendance.objects.get_or_create(user=user, date=today)

    # 🟢 Case 1 — First check-in
    if not attendance.check_in:
        attendance.check_in = now_time
        attendance.status = "Checked In"  #   mark as Checked In
        attendance.save()
        return "check_in", now_time.strftime("%H:%M:%S"), attendance.check_in

    # 🟢 Case 2 — Then check-out
    elif not attendance.check_out:
        attendance.check_out = now_time
        attendance.status = "Present"  #   change status to Present
        attendance.save()
        return "check_out", now_time.strftime("%H:%M:%S"), attendance.check_in

    # 🟢 Case 3 — Already marked fully
    else:
        return "completed", None, attendance.check_in

    
from datetime import date, timedelta
from django.db.models import Q
from .models import Attendance, MonthlyAttendance, LeaveRequest

def update_monthly_attendance(user, target_date=None):
    if target_date is None:
        target_date = date.today()

    year = target_date.year
    month = target_date.month

    # Get all attendance records for this month
    records = Attendance.objects.filter(
        user=user,
        date__year=year,
        date__month=month
    )

    # --- Counters ---
    total_days = 0
    present_days = 0
    absent_days = 0
    leave_days = 0
    holidays = 0

    # Get leave ranges (approved)
    approved_leaves = LeaveRequest.objects.filter(
        user=user, status="Approved"
    )

    leave_dates = set()
    for leave in approved_leaves:
        current = leave.start_date
        while current <= leave.end_date:
            if current.year == year and current.month == month:
                leave_dates.add(current)
            current += timedelta(days=1)

    # Count all days in the month
    from calendar import monthrange
    total_days = monthrange(year, month)[1]

    # Loop through each day of month
    for day in range(1, total_days + 1):
        current_date = date(year, month, day)
        weekday = current_date.weekday()  # 0=Mon ... 6=Sun

        # Weekends = Holiday
        if weekday in (5, 6):  # Saturday or Sunday
            holidays += 1
            continue

        # Leave days
        if current_date in leave_dates:
            leave_days += 1
            continue

        # Attendance record check
        att = records.filter(date=current_date).first()
        if att:
            if att.status in ["Present", "Checked In"]:
                present_days += 1
            elif att.status in ["Absent", "Absent (No Check-Out)"]:
                absent_days += 1
            elif att.status == "Leave":
                leave_days += 1
            elif att.status == "Holiday":
                holidays += 1
        else:
            absent_days += 1

    # Compute percentage safely
    working_days = total_days - holidays
    if working_days > 0:
        percentage = round((present_days / working_days) * 100, 2)
    else:
        percentage = 0.0

    # Save or update MonthlyAttendance record
    MonthlyAttendance.objects.update_or_create(
        user=user,
        month=month,
        year=year,
        defaults={
            "total_days": total_days,
            "present_days": present_days,
            "absent_days": absent_days,
            "leave_days": leave_days,
            "holidays": holidays,
            "percentage": percentage,
        },
    )