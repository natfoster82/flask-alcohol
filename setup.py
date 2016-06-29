from setuptools import setup

setup(name='flask_alcohol',
      version='0.1',
      description='Automatically generate API routes from Flask-SQLAlchemy models',
      url='https://github.com/natfoster82/flask-alcohol',
      author='Nat Foster',
      author_email='nat.foster@gmail.com',
      license='BSD',
      packages=['flask_alcohol'],
      install_requires=[
          'flask_sqlalchemy',
      ],
      zip_safe=False)