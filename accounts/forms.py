# accounts/forms.py
from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm

User = get_user_model()

class CustomLoginForm(AuthenticationForm):
    username = forms.CharField(widget=forms.TextInput(attrs={'placeholder': 'Username or Email'}))
    password = forms.CharField(widget=forms.PasswordInput(attrs={'placeholder': 'Password'}))

class RegistrationForm(forms.ModelForm):
    password = forms.CharField(widget=forms.PasswordInput)
    confirm_password = forms.CharField(widget=forms.PasswordInput)

    class Meta:
        model = User
        fields = ["username", "enrollment_no", "user_type", "password"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['username'].widget.attrs.update({'placeholder': 'Enter your username'})
        self.fields['enrollment_no'].widget.attrs.update({'placeholder': 'Enter your enrollment number'})
        self.fields['user_type'].widget.attrs.update({'placeholder': 'Select user type'})
        self.fields['password'].widget.attrs.update({'placeholder': 'Enter your password'})
        self.fields['confirm_password'].widget.attrs.update({'placeholder': 'Confirm your password'})

    def clean(self):
        cleaned_data = super().clean()
        password = cleaned_data.get("password")
        confirm_password = cleaned_data.get("confirm_password")

        if password != confirm_password:
            raise forms.ValidationError("Passwords do not match.")

        return cleaned_data
    
            
# class RegistrationForm(forms.ModelForm):
#     password = forms.CharField(widget=forms.PasswordInput)
#     confirm_password = forms.CharField(widget=forms.PasswordInput)

#     class Meta:
#         model = User
#         fields = ["username", "enrollment_no", "user_type", "password"]

#     def clean(self):
#         cleaned_data = super().clean()
#         password = cleaned_data.get("password")
#         confirm_password = cleaned_data.get("confirm_password")
#         # email = cleaned_data.get("institute_email")

#         if password != confirm_password:
#             raise forms.ValidationError("Passwords do not match.")

#         # if email and not email.endswith("@mycollege.edu"):  # change to your domain
#         #     raise forms.ValidationError("Use your institute email.")

#         return cleaned_data
