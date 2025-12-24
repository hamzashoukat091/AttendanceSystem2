document.addEventListener("DOMContentLoaded", function() {
  // Fade in effect
  document.body.style.opacity = 0;
  document.body.style.transition = "opacity 0.8s ease-in-out";
  setTimeout(() => { document.body.style.opacity = 1; }, 100);

  // Auto-refresh metrics every 30s
  setInterval(() => {
    fetch("/admin/dashboard/refresh/")  // new endpoint
      .then(res => res.json())
      .then(data => {
        document.getElementById("total_students").innerText = data.total_students;
        document.getElementById("total_faculty").innerText = data.total_faculty;
        document.getElementById("today_attendance").innerText = data.today_attendance;
        document.getElementById("attendance_percent").innerText = data.today_attendance_percent + "%";
      });
  }, 30000);
});
