from django.db import models
from django.contrib.auth.models import User


class EcgProcess(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    ecg_file = models.FileField(upload_to='ecg_files/')
    comment = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    result = models.TextField(blank=True, null=True)  # сюда будем сохранять результат обработки

    def __str__(self):
        return f"ECG #{self.id} by {self.user.username}"
