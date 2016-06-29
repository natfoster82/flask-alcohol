from setuptools import setup

setup(name='flask_alcohol',
      version='0.1',
      description='A framework of mixins and functions for auto-generating an API based on Flask-SQLAlchemy\'s db.Model schemas',
      url='',
      author='Nat Foster',
      author_email='nat.foster@gmail.com',
      license='BSD',
      packages=['flask_alcohol'],
      install_requires=[
          'flask_sqlalchemy',
      ],
      zip_safe=False)