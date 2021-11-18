pipeline {
    agent {
        none
    }
    options {
        skipStagesAfterUnstable()
    }
    parameters {
        booleanParam defaultValue: true, description: 'Publish to Test PyPI', name: 'PublishTestPyPI'
    }
    stages {
        stage('Build') {
            agent {
                docker {
                    label '!windows'
                    image 'python:3.9.7'
                }
            }
            steps {
                sh '''
                    python --version
                    pwd
                    df -h
                    ls -l
                '''
                sh 'python setup.py sdist bdist_wheel'
            }
        }
        stage('Test') {
            agent {
                docker {
                    label '!windows'
                    image 'python:3.9.7'
                }
            }
            steps {
                sh 'pip install --extra-index-url https://test.pypi.org/simple/ dist/tstriage-0.1.$BUILD_NUMBER-py3-none-any.whl'
                //sh 'python -m tstriage.runner -h'
            }
        }
        stage('Deploy') {
            when {
                expression {params.PublishTestPyPI == true}
            }
            agent {
                docker {
                    label '!windows'
                    image 'python:3.9.7'
                }
            }
            steps {
                sh 'pip install twine'
                withCredentials([usernamePassword(credentialsId: '65ddf05a-75ed-43cd-ab7e-5ac1e6af2526', usernameVariable: 'USERNAME', passwordVariable: 'PASSWORD')]) {
                    sh 'python -m twine upload -r testpypi dist/* -u $USERNAME -p $PASSWORD'
                }
            }
        }
    }
    post {
        aborted {
            echo 'aborted'
            emailext subject: "ABORTED: Job '${env.JOB_NAME} [${env.BUILD_NUMBER}]'",
                body: """Job "${env.JOB_NAME} [${env.BUILD_NUMBER}]" aborted\nCheck console output at ${env.BUILD_URL}\n""",
                recipientProviders: [[$class: 'DevelopersRecipientProvider'], [$class: 'RequesterRecipientProvider']]
        }
        failure {
            echo 'failure'
            emailext subject: "FAILED: Job '${env.JOB_NAME} [${env.BUILD_NUMBER}]'",
                body: """Job "${env.JOB_NAME} [${env.BUILD_NUMBER}]" failed\nCheck console output at ${env.BUILD_URL}\n""",
                recipientProviders: [[$class: 'DevelopersRecipientProvider'], [$class: 'RequesterRecipientProvider']]
        }
        success {
            echo 'success'
            emailext subject: "SUCCEEDED: Job '${env.JOB_NAME} [${env.BUILD_NUMBER}]'",
                body: """Job "${env.JOB_NAME} [${env.BUILD_NUMBER}]" succeeded\nCheck console output at ${env.BUILD_URL}\n""",
                recipientProviders: [[$class: 'DevelopersRecipientProvider'], [$class: 'RequesterRecipientProvider']]
        }
        cleanup {
            echo 'Cleaning up ...'
            sh '''
                rm -rf /var/jenkins_home/.cache/pip
                rm -rf /var/jenkins_home/.local
            '''
        }
    }
}
