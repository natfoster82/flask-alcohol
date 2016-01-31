from flask_alcohol import APIMixin, route, APIMeta, authorizes, setter, getter, adjusts_query, extra_field
from flask import Flask, jsonify, request, abort, current_app, flash, g
from flask.ext.sqlalchemy import SQLAlchemy
from flask.ext.login import current_user, UserMixin, LoginManager, AnonymousUserMixin, login_user
from datetime import datetime
from sqlalchemy.dialects.postgres import ARRAY, JSONB
from sqlalchemy.ext.hybrid import hybrid_property
from werkzeug.security import generate_password_hash, check_password_hash
from string import ascii_lowercase, digits
from os import listdir

# set up app

app = Flask(__name__)
app.config.update(
    # general Flask settings
    SECRET_KEY='exampleappsecret12345',
    DEBUG=True,
    # SQLAlchemy settings, you'll need to set up the db and run db.create_all_tables before it will work
    SQLALCHEMY_DATABASE_URI='postgres://username:password@localhost/dbname',
    SQLALCHEMY_ECHO=False,
    # Flask-Alcohol settings
    ROUTE_PREFIX='api',
    API_ERROR_MESSAGE='Please contact nat.foster@gmail.com for help'
)


db = SQLAlchemy(app)


class Guest(AnonymousUserMixin):
    def __init__(self):
        self.name = "Guest"
        self.id = 0

    def is_admin(self):
        return False

    def as_dict(self):
        return {}


login_manager = LoginManager(app)
login_manager.anonymous_user = Guest


# load user from session cookie for now
@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


# helper functions
URL_CHARS = ascii_lowercase + digits + '_-'
FRONTEND_DIR = 'static'
MEDIA_DIR = 'media'
IMAGES = ['square', 'wide', 'tall']
PREVIEW_CHARS = 300


def url_safe_string(str):
    chars = []
    for char in str.lower().replace(' ', '_'):
        if char in URL_CHARS:
            chars.append(char)
    return ''.join(chars)


def media_url(filename):
    return '/{0}/{1}'.format(MEDIA_DIR, filename)


def image_url_dict(dirname):
    try:
        available_files = listdir('{0}/{1}/{2}/'.format(FRONTEND_DIR, MEDIA_DIR, dirname))
    except FileNotFoundError:
        return {}
    url_dict = {}
    for image in IMAGES:
        try:
            filename = next((x for x in available_files if x.startswith(image)))
            url_dict[image] = '/{0}/{1}/{2}'.format(MEDIA_DIR, dirname, filename)
        except StopIteration:
            pass
    return url_dict


def image_url_array(dirname):
    try:
        available_files = listdir('{0}/{1}/{2}/'.format(FRONTEND_DIR, MEDIA_DIR, dirname))
    except FileNotFoundError:
        return []
    url_array = [x for x in available_files]
    url_array.sort()
    return url_array


# models and views, together!
class User(db.Model, UserMixin, APIMixin):
    __tablename__ = 'users'
    __autoroutes__ = ['index', 'meta']

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.Unicode, nullable=False, default='', index=True)
    pw_hash = db.Column(db.Unicode, nullable=False, default='', info={'public': False})
    first_name = db.Column(db.Unicode(50), nullable=False, default='')
    last_name = db.Column(db.Unicode(50), nullable=False, default='')
    profile_picture = db.Column(db.Unicode, nullable=False, default='', info={'public': False})
    description = db.Column(db.UnicodeText, nullable=False, default='')
    roles = db.Column(ARRAY(db.Unicode), default=[])

    posts = db.relationship('Post', backref=db.backref('author', lazy='joined', info={'public': True}), order_by='Post.id')

    def is_admin(self):
        return 'admin' in self.roles

    def full_name(self):
        return self.first_name + ' ' + self.last_name

    def abbr_name(self):
        return self.first_name[0] + '. ' + self.last_name

    def set_password(self, password):
        self.pw_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.pw_hash, password)

    # custom routes can use the route decorator
    @classmethod
    @route('/me')
    def me(cls):
        return jsonify(current_user.as_dict())

    @classmethod
    @route('/login', methods=['POST'])
    def login(cls):
        email = request.json.get('email')
        user = cls.query.filter(db.func.lower(User.email) == db.func.lower(email)).first()
        if user is None:
            abort(404, 'No user with this email address')
        if not user.check_password(request.json.get('password')):
            abort(403, 'This password doesn\'t work')
        login_user(user)
        return jsonify(user.as_dict())

    # the extra_field decorator is preferred, but more_json can also be used to add data to the response
    def more_json(self):
        return {
            'profile_picture_url': media_url(self.profile_picture) if self.profile_picture else None,
            'is_admin': self.is_admin(),
            'full_name': self.full_name(),
            'abbr_name': self.abbr_name()
        }


class Project(db.Model, APIMixin):
    __tablename__ = 'projects'
    __autoroutes__ = ['index', 'get', 'post', 'put', 'delete', 'meta']
    __idattr__ = 'slug'

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.Unicode, nullable=False, default='', info={'set_by': 'json'})
    _slug = db.Column('slug', db.Unicode, nullable=False, default='', index=True, info={'set_by': 'json'})
    description = db.Column(db.UnicodeText, nullable=False, default='', info={'set_by': 'json'})
    image_dir = db.Column(db.Unicode, nullable=False, default='', info={'set_by': 'json'})
    images = db.Column(JSONB, nullable=False, default={}, info={'set_by': 'server'})
    theme = db.Column(db.Unicode(20), nullable=False, default='default', info={'set_by': 'json'})

    posts = db.relationship('Post', order_by='Post.id', info={'public': True})

    @staticmethod
    @authorizes('post', 'put', 'delete')
    def authorize_changes(resource):
        return current_user.is_admin()

    # using SQLAlchemy's hybrid_property to provide a setter and validation step,
    # below I use Flask-Alcohol's setter decorator to do the same thing
    @hybrid_property
    def slug(self):
        return self._slug

    @slug.setter
    def slug(self, value):
        value = request.json.get('slug') or request.json.get('title')
        slug = url_safe_string(value)[:50]
        if slug != self._slug and self.__class__.query.filter_by(slug=slug).first():
            g.failed_validation = True
            flash('This slug is not unique')
            return None
        self._slug = slug

    @setter('theme')
    def set_theme(self, name, value):
        if not value:
            self.theme = 'default'
        else:
            self.theme = value

    @setter('images')
    def set_images(self, name, value):
        self.images = image_url_dict(self.image_dir)


class Post(db.Model, APIMixin):
    __tablename__ = 'posts'
    __autoroutes__ = ['index', 'get', 'post', 'put', 'delete', 'meta']
    __idattr__ = 'slug'

    id = db.Column(db.Integer, primary_key=True)
    author_id = db.Column(db.Integer, db.ForeignKey(User.id, ondelete='SET NULL'), index=True, info={'set_by': 'json'})
    project_id = db.Column(db.Integer, db.ForeignKey(Project.id, ondelete='CASCADE'), index=True, info={'set_by': 'json'})
    order = db.Column(db.Integer, nullable=False, default=0, index=True, info={'set_by': 'json'})
    slug = db.Column(db.Unicode, nullable=False, default='', index=True, info={'set_by': 'server'})
    title = db.Column(db.Unicode, nullable=False, default='', info={'set_by': 'json'})
    image_dir = db.Column(db.Unicode, nullable=False, default='', info={'set_by': 'json'})
    images = db.Column(JSONB, nullable=False, default={}, info={'set_by': 'server'})
    body = db.Column(db.UnicodeText, nullable=False, default='', info={'set_by': 'json'})
    description = db.Column(db.UnicodeText, nullable=False, default='', info={'set_by': 'json'})
    first_published_at = db.Column(db.DateTime, index=True, nullable=False, default=datetime.utcnow, info={})
    last_published_at = db.Column(db.DateTime, index=True, info={'set_by': 'server'})

    project = db.relationship('Project', lazy='joined', info={'public': True})

    @extra_field(info={'defer': True})
    def preview(self, *args):
        preview_chars = int(request.args.get('preview_chars') or current_app.config.get('PREVIEW_CHARS'))
        if len(self.body) > preview_chars:
            end_index = self.body.rfind(' ', 0, preview_chars)
            content = self.body[:end_index]
            return content
        else:
            return self.body

    @extra_field(info={'defer': True})
    def is_cut(self, *args):
        preview_chars = int(request.args.get('preview_chars') or current_app.config.get('PREVIEW_CHARS'))
        return len(self.body) > preview_chars

    @setter('slug')
    def set_slug(self, name, value):
        # validates slug exists and is unique
        value = request.json.get('slug') or request.json.get('title')
        slug = url_safe_string(value)[:50]
        if slug != self.slug and self.__class__.query.filter_by(slug=slug).first():
            g.failed_validation = True
            flash('This slug is not unique')
            return None
        self.slug = slug

    @setter('images')
    def set_images(self, name, value):
        print('setting images for post')
        self.images = image_url_dict(self.image_dir)

    @getter('first_published_at', 'last_published_at')
    def get_isoformat(self, name):
        val = getattr(self, name)
        return val.isoformat()

    @setter('last_published_at')
    def set_last_published_at(self, name, value):
        now = datetime.utcnow()
        if request.json.get('publish'):
            self.last_published_at = now
        else:
            self.last_published_at = None

    @staticmethod
    @authorizes('post', 'put', 'delete')
    def authorize_changes(resource):
        return current_user.is_admin()

    @staticmethod
    @adjusts_query('get', 'index')
    def relevant_posts(query):
        if not current_user.is_admin():
            query = query.filter(Post.last_published_at != None)
        project_id = request.args.get('project_id')
        if project_id:
            query = query.filter(Post.project_id == project_id)
        return query


class Gallery(db.Model, APIMixin):
    __tablename__ = 'galleries'
    __autoroutes__ = ['index', 'get', 'post', 'put', 'delete', 'meta']

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey(Project.id, ondelete='CASCADE'), index=True, info={'set_by': 'json'})
    title = db.Column(db.Unicode, nullable=False, default='', info={'set_by': 'json'})
    image_dir = db.Column(db.Unicode, nullable=False, default='', info={'set_by': 'json'})
    images = db.Column(ARRAY(db.Unicode), nullable=False, default=[], info={'set_by': 'server'})

    @setter('images')
    def set_images(self, name, value):
        self.images = image_url_array(self.image_dir)

    @staticmethod
    @authorizes('post', 'put', 'delete')
    def authorize_changes(resource):
        return current_user.is_admin()


User.register(app)
Project.register(app)
Post.register(app)
Gallery.register(app)
APIMeta.register(app)


if __name__ == '__main__':
    app.run(port=1999, threaded=True)
