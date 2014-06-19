from flask import Flask
from flask import request
from flask import make_response
app = Flask(__name__)
app.config['DEBUG'] = True

from google.appengine.ext import ndb
from google.appengine.api import memcache
import logging
import datetime
import cloudstorage
import json

SECONDS_BETWEEN_UPDATES = 60
SENSOR_TIMEOUT_IN_SECONDS = 300
BUCKET_NAME = "/planar-contact-601.appspot.com/"
ALLOWED_EXTENSIONS = set(['png', 'jpg', 'jpeg'])
UPDATE_FLAG = 'database_updated_recently'

class DataPoint(ndb.Model):
    value = ndb.FloatProperty()
    date = ndb.DateTimeProperty(auto_now_add=True)
    @classmethod
    def points_for_sensor(cls,sensor_key):
        return cls.query(ancestor=sensor_key).order(-cls.date)
    @classmethod
    def oldest_points(cls):
        return cls.query().order(+cls.date)

class Sensor(ndb.Model):
    @classmethod
    def sensor_list(cls):
        return cls.query()

def update():
    # Check to see if we have updated recently
    update_flag = memcache.get(UPDATE_FLAG)
    if update_flag is not None:
        return
    memcache.add(UPDATE_FLAG,'YES',SECONDS_BETWEEN_UPDATES)
    # Delete old data points
    now = datetime.datetime.now()
    count = 0
    for point in DataPoint.oldest_points().iter():
        delta = (now-point.date).total_seconds()
        if delta > 3600:
            point.key.delete()
            count += 1
        else:
            break
    logging.info("Deleted {} old data points".format(count))
    # Loop through all of the sensors
    sensor_names = []
    for sensor in Sensor.sensor_list().iter():
        # Store current values of each sensor
        sensor_value = memcache.get(sensor.key.id()+"_value")
        if sensor_value is not None:
            point = DataPoint(parent=sensor.key,value=sensor_value)
            point.put()
        # Delete sensors that don't have any data
        curr_points = DataPoint.points_for_sensor(sensor.key)
        if curr_points.count() == 0 and sensor_value is not None:
            logging.info("Deleting sensor {}".format(sensor.key.id()))
            sensor.key.delete()
        else:
            sensor_names.append(sensor.key.id())
    # Store a list of sensor names in memcache
    memcache.set("sensor_names",",".join(sensor_names))

@app.route('/',methods=['POST'])
@app.route('/sensor_values',methods=['GET','POST'])
def process_values():
    if request.method == 'GET':
        sensor_names = memcache.get('sensor_names')
        if sensor_names is None:
            sensor_names = ",".join([sensor.key.id() for sensor in Sensor.sensor_list()])
            memcache.set('sensor_names',sensor_names)
        sensor_names = sensor_names.split(",")
        sensor_values = {}
        for sensor_name in sensor_names:
            sensor_value = memcache.get(sensor_name+'_value')
            if sensor_value is not None:
                sensor_values[sensor_name] = sensor_value
        return json.dumps(sensor_values)
    elif request.method == 'POST':
        update()
        logging.info(request.values)
        sensor_name = request.form.get('sensor')
        sensor_value = float(request.form.get('value'))
        sensor_names = memcache.get('sensor_names')
        if sensor_names is not None and sensor_name not in sensor_names:
            sensor = Sensor()
            sensor.key = ndb.Key('Sensor',sensor_name)
            sensor.put()
            sensor_names = sensor_name if sensor_names == "" else sensor_names + "," + sensor_name
            memcache.set('sensor_names',sensor_names)
        memcache.set(sensor_name+'_value',sensor_value,SENSOR_TIMEOUT_IN_SECONDS)
        return "Success\n"

@app.route('/sensor_names',methods=['GET'])
def print_names():
    return json.dumps([sensor.key.id() for sensor in Sensor.sensor_list()])

@app.route('/graphing_data',methods=['GET'])
def graphing_data():
    last_timestamp = request.args.get('last_timestamp')
    sensor_name = request.args.get('sensor')
    if last_timestamp is None:
        last_timestamp = 0
    else:
        last_timestamp = float(last_timestamp)
    points = DataPoint.points_for_sensor(ndb.Key('Sensor',sensor_name))
    epoch = datetime.datetime(1970,1,1)
    res = []
    for point in points.iter():
        timestamp = (point.date-epoch).total_seconds()
        if timestamp > last_timestamp:
            _res = {"timestamp":timestamp, "value":point.value}
            res.append(_res)
        else:
            break
    res.reverse()
    return json.dumps(res)

@app.route('/clear',methods=['GET'])
def clear_data():
    points = DataPoint.query()
    for point in points:
        point.key.delete()
    for sensor in Sensor.sensor_list():
        sensor.key.delete()
    return "Success"

@app.route('/picture/submit',methods=['GET','POST'])
def set_picture():
    if request.method == 'POST':
        _file = request.files['Image.jpg']
        if _file:
            cloud_file = cloudstorage.open(BUCKET_NAME+"microscope_image."+_file.filename.rsplit('.',1)[1],mode='w',content_type=_file.mimetype)
            _file.save(cloud_file)
            cloud_file.close()
            return "Success"
    else:
        return '''
        <!doctype html>
        <title>Upload microscope file</title>
        <h1>Upload microscope file</h1>
        <form action="" method=post enctype=multipart/form-data>
            <p><input type=file name=file>
                <input type=submit value=Upload>
        </form>
        '''

@app.route('/picture/view',methods=['GET'])
def view_picture():
    for _file in cloudstorage.listbucket(BUCKET_NAME):
        if "microscope_image" in _file.filename:
            _file = cloudstorage.stat(_file.filename)
            logging.info(_file.filename)
            logging.info(_file.content_type)
            cloud_file = cloudstorage.open(_file.filename,mode='r')
            response = make_response(cloud_file.read())
            cloud_file.close()
            response.mimetype = _file.content_type
            return response
    return "No file found"

@app.errorhandler(404)
def page_not_found(e):
    """Return a custom 404 error."""
    return 'Sorry, nothing at this URL.', 404
