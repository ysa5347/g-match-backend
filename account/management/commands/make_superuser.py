from django.core.management.base import BaseCommand
from account.models import CustomUser


class Command(BaseCommand):
    help = 'Make an existing user a superuser by email'

    def add_arguments(self, parser):
        parser.add_argument('email', type=str, help='Email of the user to make superuser')

    def handle(self, *args, **options):
        email = options['email']
        
        try:
            user = CustomUser.objects.get(email=email)
            user.is_staff = True
            user.is_superuser = True
            user.save()
            
            self.stdout.write(
                self.style.SUCCESS(f'Successfully made {email} a superuser')
            )
        except CustomUser.DoesNotExist:
            self.stdout.write(
                self.style.ERROR(f'User with email {email} does not exist')
            )
