from django import forms

class UploadFileForm(forms.Form):
    archivo = forms.FileField(
        label='Archivo Excel de Activos',
        help_text='Sube el archivo .xlsx con las columnas requeridas.'
    )
